#!/usr/bin/env python3
"""Locked raw-native-model benchmark with deliberately narrow semantic checks.

No application router, calculator, retrieval, replacement answer, or network is
used. The code metric checks only the stated supported HTML/JS subset.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def normalize(text):
    return ' '.join(text.split())


def numeric_answer(text):
    number = r'[-+]?\d+(?:[.,]\d+)?'
    labels = list(re.finditer(r'\bОтвет\s*:\s*(?:x\s*=\s*)?', text, re.I))
    if labels:
        match = re.match('(' + number + r')(?=\s*(?:$|[.!?;](?!\s*\d)))', text[labels[-1].end():])
        return float(match.group(1).replace(',', '.')) if match else None
    if re.fullmatch(r'\s*(?:x\s*=\s*)?' + number + r'[.!]?\s*', text):
        return float(re.search(number, text).group().replace(',', '.'))
    return None


def _payload(text):
    value = re.sub(r'^\s*Ответ\s*:\s*', '', text, count=1, flags=re.I).strip()
    return value[:-1] if value.endswith('.') else value


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {'tag':'root', 'attrs':{}, 'children':[]}
        self.stack = [self.root]
        self.valid = True

    def handle_starttag(self, tag, attrs):
        if len(dict(attrs)) != len(attrs) or tag not in {'section','div','article','h1','h2','h3','button','script'}:
            self.valid = False
        node = {'tag':tag,'attrs':dict(attrs),'children':[]}
        self.stack[-1]['children'].append(node)
        self.stack.append(node)

    def handle_endtag(self, tag):
        if len(self.stack) < 2 or self.stack[-1]['tag'] != tag:
            self.valid = False
        else:
            self.stack.pop()

    def handle_startendtag(self, tag, attrs):
        self.valid = False

    def handle_data(self, data):
        self.stack[-1]['children'].append(data)

    def handle_comment(self, data):
        self.valid = False

    def handle_decl(self, decl):
        self.valid = False

    def unknown_decl(self, data):
        self.valid = False

    def finish(self, code):
        try:
            self.feed(code)
            self.close()
        except Exception:
            self.valid = False
        return self.valid and len(self.stack) == 1


def _nodes(node):
    for child in node['children']:
        if isinstance(child, dict):
            yield child
            yield from _nodes(child)


def _text(node):
    return ''.join(_text(c) if isinstance(c,dict) else c for c in node['children'])


def _children(node):
    return [c for c in node['children'] if isinstance(c,dict) or c.strip()]


def _fenced(text):
    matches = re.findall(r'```(?:html|javascript|js)?[ \t]*\r?\n(.*?)```', text, re.S | re.I)
    return matches[0].strip() if len(matches) == 1 else None


def _code_pass(task, text):
    code = _fenced(text)
    if code is None:
        return False
    slots, topic = task['slots'], task['topic']
    if topic == 'function':
        name, inc, value = re.escape(slots['name']), slots['increment'], slots['input']
        # Full-match rejects hidden branches, wrong operators, comments, extra
        # statements and accidental occurrences of the desired parameter text.
        pattern = (rf'function\s+{name}\s*\(\s*([A-Za-z_$][\w$]*)\s*\)\s*\{{\s*'
                   rf'return[ \t]+\1\s*\+\s*{inc}\s*;?\s*\}}\s*'
                   rf'console\s*\.\s*log\s*\(\s*{name}\s*\(\s*{value}\s*\)\s*\)\s*;?')
        match = re.fullmatch(pattern, code)
        return match is not None and _js_identifier(match.group(1))
    parser = _HTML()
    if not parser.finish(code):
        return False
    top = _children(parser.root)
    nodes = list(_nodes(parser.root))
    if topic == 'card':
        if len(top) != 1 or not isinstance(top[0],dict) or top[0]['tag'] not in {'section','div','article'}:
            return False
        container = top[0]
        if set(container['attrs']) != {'style'}:
            return False
        declarations = [p.strip() for p in (container['attrs']['style'] or '').split(';') if p.strip()]
        styles = {}
        for declaration in declarations:
            if declaration.count(':') != 1:
                return False
            key,value = (x.strip().lower() for x in declaration.split(':'))
            if key in styles:
                return False
            styles[key] = value
        if styles != {'color':slots['color'].lower(), 'padding':str(slots['padding'])+'px'}:
            return False
        children = _children(container)
        if len(children) != 1 or not isinstance(children[0],dict):
            return False
        heading = children[0]
        return (heading['tag'] in {'h1','h2','h3'} and not heading['attrs']
                and all(isinstance(c,str) for c in heading['children'])
                and normalize(_text(heading)) == normalize(slots['title']) and len(nodes)==2)
    if topic == 'counter':
        if len(top)!=2 or not all(isinstance(c,dict)for c in top) or [c['tag']for c in top]!=['button','script']:
            return False
        button,script = top
        if set(button['attrs']) - {'id','type'} or button['attrs'].get('type','button')!='button' or script['attrs']:
            return False
        identifier = button['attrs'].get('id','')
        if not re.fullmatch(r'[A-Za-z_][\w-]*',identifier) or any(isinstance(c,dict)for c in button['children']):
            return False
        if normalize(_text(button)) != str(slots['initial']) or len(nodes)!=2:
            return False
        initial,step = slots['initial'],slots['step']
        # Groups: numeric variable, DOM variable, matching quotation mark.
        pattern = (rf'(?:let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*{initial}\s*;\s*'
                   rf'(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*document\s*\.\s*getElementById\s*'
                   rf'\(\s*([\'\"]){re.escape(identifier)}\3\s*\)\s*;\s*'
                   rf'\2\s*\.\s*onclick\s*=\s*\(\s*\)\s*=>\s*\{{\s*'
                   rf'\1\s*\+=\s*{step}\s*;\s*\2\s*\.\s*textContent\s*=\s*\1\s*;?\s*\}}\s*;?')
        match = re.fullmatch(pattern,_text(script).strip())
        return (match is not None and match.group(1)!=match.group(2)
                and _js_identifier(match.group(1)) and _js_identifier(match.group(2)))
    return False


def _js_identifier(value):
    reserved = {'await','break','case','catch','class','const','continue','debugger','default',
                'delete','do','else','enum','export','extends','false','finally','for','function',
                'if','implements','import','in','instanceof','interface','let','new','null','package',
                'private','protected','public','return','static','super','switch','this','throw',
                'true','try','typeof','var','void','while','with','yield'}
    return value not in reserved


def grade(row, text):
    task = row['task']
    kind = task['kind']
    if kind in ('arithmetic','linear'):
        stated = numeric_answer(text)
        return {'metric':'stated_numeric_answer','passed':stated is not None and stated==task['result'],
                'expected':task['result'],'stated':stated}
    if kind in ('copy','multiturn'):
        expected = str(task['value'] if kind=='copy' else task['target'])
        return {'metric':'exact_terminal_payload','passed':_payload(text)==expected,'expected':expected}
    if kind == 'authored':
        return {'metric':'known_reference_whitespace_match',
                'passed':normalize(text)==normalize(row['messages'][-1]['content'])}
    if kind == 'code':
        return {'metric':'code_slots_satisfied','passed':_code_pass(task,text),
                'supported_subset':task['topic']}
    raise ValueError(f'Unsupported task kind: {kind}')


def prompt_for(row):
    result = ''
    for message in row['messages'][:-1]:
        if message['role'] not in ('user','assistant') or any(m in message['content']for m in ('<|im_start|>','<|im_end|>')):
            raise ValueError('Invalid benchmark dialogue')
        result += '<|im_start|>'+message['role']+'\n'+message['content']+'<|im_end|>\n'
    return result+'<|im_start|>assistant\n'


def benchmark(args):
    data = args.data.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != args.expected_data_sha256:
        raise ValueError('Locked benchmark SHA-256 does not match')
    rows = [json.loads(line)for line in data.decode('utf-8').splitlines()if line.strip()]
    if len(rows)!=100 or len({r['id']for r in rows})!=100 or any(r['split']!='test'for r in rows):
        raise ValueError('Expected exactly 100 unique frozen final-test cases')
    # Verify every reference against the criteria before seeing any generation.
    if not all(grade(r,r['messages'][-1]['content'])['passed']for r in rows):
        raise ValueError('A frozen reference fails the declared scorer')
    model_digest,runner_digest = sha256(args.model),sha256(args.runner)
    started = time.monotonic()
    answers = []
    with tempfile.TemporaryDirectory(prefix='weqrom-raw-benchmark-') as temporary:
        temporary = Path(temporary)
        model,runner = temporary/'frozen.gguf',temporary/'frozen-runner'
        shutil.copy2(args.model,model)
        shutil.copy2(args.runner,runner)
        if sha256(model)!=model_digest or sha256(runner)!=runner_digest:
            raise ValueError('Model or runner changed while freezing benchmark inputs')
        for index,row in enumerate(rows):
            prompt = temporary/'prompt.txt'
            prompt.write_text(prompt_for(row),encoding='utf-8')
            began = time.monotonic()
            error = None
            returncode = None
            try:
                result = subprocess.run([str(runner),'--model',str(model),'--prompt-file',str(prompt),
                                         '--tokens','128','--finish-reason'],capture_output=True,
                                        timeout=10,check=False)
                stdout = result.stdout.decode('utf-8',errors='replace')
                stderr = result.stderr.decode('utf-8',errors='replace')
                returncode = result.returncode
                if returncode:
                    error = 'runner_exit_'+str(returncode)
            except subprocess.TimeoutExpired as failure:
                stdout = (failure.stdout or b'').decode('utf-8',errors='replace')
                stderr = (failure.stderr or b'').decode('utf-8',errors='replace')
                error = 'runtime_timeout_10_seconds'
            elapsed = time.monotonic()-began
            grading = grade(row,stdout) if error is None else None
            answers.append({'id':row['id'],'category':row['category'],'task':row['task'],
                            'messages':row['messages'],'prompt_sha256':sha256(prompt),
                            'raw_stdout':stdout,'raw_stderr':stderr,'runtime_seconds':elapsed,
                            'returncode':returncode,'runtime_error':error,'grade':grading})
            print(json.dumps({'completed':index+1,'total':100,'category':row['category'],
                              'runtime_error':error,'passed':grading['passed']if grading else None}),flush=True)
    categories = defaultdict(lambda:Counter(total=0,evaluated=0,passed=0,runtime_errors=0))
    metrics = defaultdict(lambda:Counter(evaluated=0,passed=0))
    for answer in answers:
        count = categories[answer['category']]
        count['total'] += 1
        if answer['grade'] is None:
            count['runtime_errors'] += 1
        else:
            count['evaluated'] += 1
            count['passed'] += int(answer['grade']['passed'])
            metrics[answer['grade']['metric']]['evaluated'] += 1
            metrics[answer['grade']['metric']]['passed'] += int(answer['grade']['passed'])
    report = {'schema_version':1,'benchmark':'weqrom-v2-frozen-raw-native-100',
              'model_sha256':model_digest,'runner_sha256':runner_digest,'data_sha256':digest,
              'scorer_sha256':sha256(Path(__file__)),'model_path':str(args.model),'runner_path':str(args.runner),
              'generation':{'max_tokens':128,'case_timeout_seconds':10,'sampling':'native greedy',
                            'format':'ChatML without system; complete supplied history'},
              'runtime_seconds':time.monotonic()-started,'cases':100,
              'runtime_errors':sum(a['runtime_error']is not None for a in answers),
              'by_category':dict(categories),'by_metric':dict(metrics),
              'limits':['Runtime failures are not graded as model-answer mistakes; compare successful-case denominators and runtime errors separately.',
                        'Code score checks only the declared counter/card/function subset and requested slots, not arbitrary program correctness.',
                        'Facts test known answers under held-out wording, not new factual knowledge.',
                        'This final sample has no division cases and does not establish general reasoning ability.'],
              'answers':answers}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items()if k!='answers'},ensure_ascii=False,indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=Path('/workspace/.chits-tools/weqrom-corpus-v2/test.jsonl'))
    parser.add_argument('--expected-data-sha256',required=True)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--runner',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    try:
        benchmark(args)
    except (ValueError,OSError) as error:
        parser.exit(1,f'benchmark-v2: {error}\n')


if __name__ == '__main__':
    main()
