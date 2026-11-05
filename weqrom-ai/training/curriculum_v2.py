#!/usr/bin/env python3
"""Grounded, AI-assisted original curriculum; preserves the V1 data unchanged."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

from corpus import AUTHORED

SEED = 20261105
OLD_DATA = Path('/workspace/.chits-tools/weqrom-corpus')
SPLITS = ('train', 'validation', 'test')


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _partition(seed, key):
    bucket = int(_hash([seed, key])[:8], 16) % 100
    return 'test' if bucket < 10 else 'validation' if bucket < 20 else 'train'


def _read_old(directory=OLD_DATA):
    result = {}
    for split in ('train', 'validation'):
        path = Path(directory) / (split + '.jsonl')
        if not path.is_file():
            raise ValueError(f'Original V1 data required for leakage exclusions: {path}')
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        result[split] = {
            'prompts': {row['messages'][0]['content'] for row in rows},
            'pairs': {tuple(sorted(row['task']['operands'])) for row in rows
                      if row['task']['kind'] == 'arithmetic'},
            'linear_triples': {tuple(row['task']['operands']) for row in rows
                               if row['task']['kind'] == 'linear'},
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return result


def _numeric_reservations(seed, old):
    # Freeze twenty genuinely new small operand pairs for the final benchmark.
    # V1 already saw 199 of the 231 canonical pairs in 0..20; blindly hashing
    # them would leave almost no final cases unseen by the baseline model.
    unseen = [(a, b) for a in range(21) for b in range(a, 21)
              if (a, b) not in old['train']['pairs']]
    unseen.sort(key=lambda pair: _hash([seed, 'reserve-small', pair]))
    if len(unseen) < 24:
        raise ValueError('Insufficient V1-unseen small pairs for an independent benchmark')
    divisible = [pair for pair in unseen if pair[0] and pair[1] % pair[0] == 0]
    reserved_test = set(divisible[:5])
    for pair in unseen:
        if len(reserved_test) >= 20:
            break
        reserved_test.add(pair)
    return {'test': reserved_test, 'validation': set(unseen) - reserved_test}


def _numeric_split(seed, pair, reservations):
    for split, values in reservations.items():
        if pair in values:
            return split
    if pair[1] <= 20:
        return 'train'
    return _partition(seed, ['arithmetic', pair])


def _row(split, category, family, question, answer, task, history=None):
    messages = list(history or []) + [{'role': 'user', 'content': question},
                                      {'role': 'assistant', 'content': answer}]
    prompt = '\n'.join(message['content'] for message in messages if message['role'] == 'user')
    return {'id': 'weqrom-v2-' + _hash([category, prompt])[:20], 'split': split,
            'category': category, 'family': family, 'messages': messages, 'task': task}


def _math(rng, seed, split, old, reservations):
    operation = rng.choice(['add', 'subtract', 'multiply', 'divide'])
    if split == 'test':
        a, b = rng.choice(sorted(reservations['test']))
        if rng.random() < .5:
            a, b = b, a
    else:
        limit = 20 if rng.random() < .8 else 99
        a, b = rng.randint(0, limit), rng.randint(0, limit)
    if operation == 'divide':
        if not b or a % b:
            return None
        result, symbol = a // b, ':'
    elif operation == 'multiply':
        result, symbol = a * b, '×'
    elif operation == 'subtract':
        result, symbol = a - b, '-'
    else:
        result, symbol = a + b, '+'
    pair = tuple(sorted((a, b)))
    if _numeric_split(seed, pair, reservations) != split:
        return None
    if split == 'train' and pair in old['validation']['pairs']:
        return None
    templates = {
        'train': ['Вычисли {a} {s} {b}.', 'Реши: {a} {s} {b}.', 'Сколько получится {a} {s} {b}?',
                  'Найди число: {a} {s} {b}.', 'Дай результат выражения {a} {s} {b}.',
                  'Помоги посчитать {a} {s} {b}.', 'Пример для вычисления: {a} {s} {b}.',
                  'Какой результат у {a} {s} {b}?'],
        'validation': ['Посчитай значение: {a} {s} {b}.', 'Укажи ответ для {a} {s} {b}.',
                       'Чему равно выражение {a} {s} {b}?'],
        'test': ['Проверь новый пример {a} {s} {b}: какой ответ?',
                 'Запиши итог вычисления {a} {s} {b}.'],
    }[split]
    index = rng.randrange(len(templates))
    question = templates[index].format(a=a, b=b, s=symbol)
    return _row(split, 'arithmetic', f'math.{split}.{index}', question, f'Ответ:\n{result}',
                {'kind': 'arithmetic', 'operation': operation, 'operands': [a, b], 'result': result,
                 'split_key': 'arithmetic:' + json.dumps(pair),
                 'seen_operand_pair_in_v1_train': pair in old['train']['pairs']})


def _copy(rng, seed, split):
    field = rng.choices(['number', 'identifier', 'word'], [7, 2, 1])[0]
    if field == 'number':
        value = str(rng.randint(-199, 999))
    elif field == 'identifier':
        value = rng.choice(['item', 'score', 'player', 'button', 'box']) + str(rng.randint(1, 399))
    else:
        value = rng.choice(['мяч', 'солнце', 'карта', 'задача', 'игра', 'книга', 'зелёный', 'привет']) + str(rng.randint(1, 99))
    key = ['copy', field, value]
    if _partition(seed, key) != split:
        return None
    templates = {
        'train': ['Повтори без изменений: {v}', 'Перепиши значение {v}.', 'Верни только {v}.',
                  'Моё значение: {v}. Напиши его.', 'Скопируй эту запись: {v}',
                  'Дано значение {v}. Повтори его точно.', 'Какое значение я указал: {v}?',
                  'Из строки «значение={v}» извлеки значение.'],
        'validation': ['Выпиши значение из записи «данные: {v}».', 'Сохрани запись в точности: {v}',
                       'Мне нужно повторение строки {v}.'],
        'test': ['Без изменения символов выведи следующую запись: {v}',
                 'Какой текст находится после двоеточия в «поле: {v}»?'],
    }[split]
    index = rng.randrange(len(templates))
    return _row(split, 'copy', f'copy.{split}.{index}', templates[index].format(v=value),
                'Ответ:\n' + value, {'kind': 'copy', 'field': field, 'value': value,
                                     'split_key': json.dumps(key, ensure_ascii=False)})


def _code(rng, seed, split):
    topic = rng.choice(['counter', 'card', 'function'])
    if topic == 'counter':
        slots = {'initial': rng.randint(0, 49), 'step': rng.randint(1, 9)}
        spec = f"счётчик: начальное число {slots['initial']}, прибавление при клике {slots['step']}"
        code = f"<button id=\"b\">{slots['initial']}</button>\n<script>\nlet n={slots['initial']};\nconst b=document.getElementById('b');\nb.onclick=()=>{{n+={slots['step']};b.textContent=n;}};\n</script>"
        language = 'html'
    elif topic == 'card':
        slots = {'title': rng.choice(['Игра', 'Заметки', 'Профиль', 'Проект']) + ' ' + str(rng.randint(1, 299)),
                 'color': rng.choice(['green', 'black', 'white', 'red', 'blue', 'orange']),
                 'padding': rng.randint(0, 40)}
        spec = f"HTML-карточка: заголовок «{slots['title']}», цвет {slots['color']}, внутренний отступ {slots['padding']}px"
        code = f"<section style=\"color:{slots['color']};padding:{slots['padding']}px\"><h2>{slots['title']}</h2></section>"
        language = 'html'
    else:
        slots = {'name': rng.choice(['grow', 'add', 'increase', 'next']) + str(rng.randint(1, 199)),
                 'increment': rng.randint(1, 9), 'input': rng.randint(0, 49)}
        spec = f"JS-функция {slots['name']}(x), прибавляющая {slots['increment']}; покажи вызов с аргументом {slots['input']}"
        code = f"function {slots['name']}(x){{return x+{slots['increment']};}}\nconsole.log({slots['name']}({slots['input']}));"
        language = 'javascript'
    key = ['code', topic, slots]
    if _partition(seed, key) != split:
        return None
    templates = {
        'train': ['Напиши короткий код. Нужно: {s}.', 'Покажи код по точным параметрам: {s}.',
                  'Сделай небольшой пример: {s}.', 'Нужен фрагмент кода: {s}.',
                  'Собери пример без библиотек: {s}.'],
        'validation': ['Реализуй следующие условия: {s}.', 'Запиши код для такого задания: {s}.'],
        'test': ['Новый заказ на пример кода: {s}. Используй указанные значения.',
                 'Составь фрагмент, соблюдая все параметры: {s}.'],
    }[split]
    index = rng.randrange(len(templates))
    return _row(split, 'code_example', f'code.{topic}.{split}.{index}', templates[index].format(s=spec),
                f'```{language}\n{code}\n```', {'kind': 'code', 'topic': topic, 'slots': slots,
                                               'split_key': json.dumps(key, ensure_ascii=False, sort_keys=True)})


def _facts(split):
    prefixes = {
        'train': ['', 'Подскажи: ', 'Расскажи: ', 'Коротко поясни: ', 'Мне нужно понять: ',
                  'Вопрос: ', 'Объясни доступно: ', 'Дай подсказку: ', 'Хочу разобраться: ', 'Помоги мне: '],
        'validation': ['Сформулируй понятный ответ: ', 'Объясни для новичка: ', 'О чём речь: '],
        'test': ['Новая формулировка вопроса: ', 'Проверим знакомую тему: '],
    }[split]
    suffixes = [' Ответь просто.', ' Достаточно короткого ответа.', ' Поясни основную мысль.',
                ' Напиши понятно.', ' Объясни в двух предложениях.']
    result = []
    for category, topic, question, answer in AUTHORED:
        for p, prefix in enumerate(prefixes):
            for s, suffix in enumerate(suffixes):
                result.append(_row(split, category, f'facts.{split}.{p}.{s}', prefix + question + suffix,
                                   answer, {'kind': 'authored', 'topic': topic,
                                            'split_key': f'facts-wording:{split}:{topic}:{p}:{s}',
                                            'known_concept_rewording': True}))
    return result


def _linear(rng, seed, split, old):
    a, b, x = rng.randint(1, 9), rng.randint(0, 20), rng.randint(0, 20)
    c = a*x+b
    key = ['linear', a, b, x]
    if split == 'train' and (a, b, x) in old['validation']['linear_triples']:
        return None
    if _partition(seed, key) != split:
        return None
    prefixes = {'train': ['Найди x: ', 'Реши уравнение '],
                'validation': ['Вычисли неизвестное в равенстве '],
                'test': ['Какое x подходит к равенству ']}[split]
    index = rng.randrange(len(prefixes))
    return _row(split, 'linear_equation', f'linear.{split}.{index}',
                prefixes[index] + f'{a}x + {b} = {c}?', f'Ответ:\nx = {x}',
                {'kind': 'linear', 'operands': [a,b,x], 'rhs': c, 'result': x,
                 'split_key': json.dumps(key)})


def _multiturn(rng, seed, split):
    value = str(rng.randint(0, 999))
    name = rng.choice(['Аня', 'Борис', 'Вера', 'Дима', 'Елена', 'Игорь'])
    key = ['multiturn', name, value]
    if _partition(seed, key) != split:
        return None
    templates = {'train': ['Какое число я назвал?', 'Повтори сохранённое число.'],
                 'validation': ['Назови число из моего предыдущего сообщения.'],
                 'test': ['Верни только число, которое я сообщил раньше.']}[split]
    index = rng.randrange(len(templates))
    return _row(split, 'multiturn', f'multiturn.{split}.{index}', templates[index],
                'Ответ:\n' + value, {'kind': 'multiturn', 'target': value,
                                     'split_key': json.dumps(key, ensure_ascii=False)},
                [{'role': 'user', 'content': f'Я {name}. Моё число {value}. Запомни его в этом диалоге.'},
                 {'role': 'assistant', 'content': f'Твоё число:\n{value}'}])


def build(seed=SEED, train_size=16000, validation_size=1000, test_size=100, old_data_dir=OLD_DATA):
    sizes = dict(zip(SPLITS, (train_size, validation_size, test_size)))
    if any(type(n) is not int or n < 1 or n > 30000 for n in sizes.values()):
        raise ValueError('Split sizes must be integers in 1..30000')
    old = _read_old(old_data_dir)
    reservations = _numeric_reservations(seed, old)
    result = {}
    global_prompts, global_ids = set(), set()
    for split, size in sizes.items():
        rng = random.Random(f'weqrom-grounded-v2:{seed}:{split}')
        fractions = ({'arithmetic':.25,'code':.25,'copy':.20,'facts':.20,'multiturn':.07,'linear':.03}
                     if split == 'train' else
                     {'arithmetic':.20,'code':.20,'copy':.20,'facts':.20,'multiturn':.10,'linear':.10})
        counts = {key: int(size*value) for key,value in fractions.items()}
        counts['arithmetic'] += size-sum(counts.values())
        facts = _facts(split)
        rng.shuffle(facts)
        accepted = []
        for kind, count in counts.items():
            done = attempts = 0
            while done < count:
                attempts += 1
                if attempts > max(100000, count*1000):
                    raise ValueError(f'Cannot generate {count} unique {split} {kind} rows')
                row = (_math(rng, seed, split, old, reservations) if kind == 'arithmetic' else
                       _code(rng, seed, split) if kind == 'code' else
                       _copy(rng, seed, split) if kind == 'copy' else
                       facts.pop() if kind == 'facts' and facts else
                       _multiturn(rng, seed, split) if kind == 'multiturn' else
                       _linear(rng, seed, split, old) if kind == 'linear' else None)
                if row is None:
                    continue
                prompt = '\n'.join(m['content'] for m in row['messages'] if m['role'] == 'user')
                if prompt in global_prompts or row['id'] in global_ids:
                    continue
                if split == 'train' and any(m['content'] in old['validation']['prompts'] for m in row['messages'] if m['role']=='user'):
                    continue
                global_prompts.add(prompt)
                global_ids.add(row['id'])
                accepted.append(row)
                done += 1
        rng.shuffle(accepted)
        result[split] = accepted
    keys = {split:{row['task']['split_key'] for row in rows if row['task']['kind'] != 'authored'}
            for split, rows in result.items()}
    for i,a in enumerate(SPLITS):
        for b in SPLITS[i+1:]:
            if keys[a] & keys[b]:
                raise ValueError('Task parameters leaked across splits')
    result['meta'] = {
        'schema_version': 2, 'name': 'weqrom-grounded-curriculum-v2', 'seed': seed,
        'counts': sizes, 'categories': {s:dict(Counter(r['category'] for r in result[s])) for s in SPLITS},
        'provenance': {'templates_authored_with_ai_assistance':True, 'downloaded_datasets':[],
                       'external_model_api_inference':False, 'pretrained_tokenizer':False,
                       'v1_train_sha256':old['train']['sha256'], 'v1_validation_sha256':old['validation']['sha256']},
        'split_policy': ['Whole canonical arithmetic pairs and code/copy/multiturn parameter tuples partition before generation',
                         'All final arithmetic operand pairs are absent from V1 training',
                         'V1 validation arithmetic pairs and exact prompts excluded from V2 training',
                         'V1 validation linear-equation parameter triples excluded from V2 training',
                         'Separate question wording families for all three splits',
                         'Facts are known concepts with new wording, not new factual knowledge',
                         'Final test is frozen before training; never used for tokenizer or checkpoint selection'],
        'small_final_operand_pairs': [list(p) for p in sorted(reservations['test'])],
        'limits': ['Synthetic narrow curriculum; not evidence of general reasoning or complex program synthesis',
                   'Assistant outputs require independent exact/functional evaluation; loss alone is insufficient'],
    }
    return result


def write(output_dir, seed=SEED, train_size=16000, validation_size=1000, test_size=100, old_data_dir=OLD_DATA):
    result = build(seed, train_size, validation_size, test_size, old_data_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    meta = result['meta']
    meta['files'] = {}
    # Refuse to silently replace an already frozen benchmark with other bytes.
    payloads = {s:(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
                         for r in result[s])).encode() for s in SPLITS}
    test = output/'test.jsonl'
    if test.exists() and test.read_bytes() != payloads['test']:
        raise ValueError('Final benchmark already frozen; use a new output directory for a different dataset')
    for split, payload in payloads.items():
        filename = split+'.jsonl'
        target = output/filename
        if not target.exists() or target.read_bytes() != payload:
            target.write_bytes(payload)
        meta['files'][filename] = {'sha256':hashlib.sha256(payload).hexdigest(),'bytes':len(payload),'examples':len(result[split])}
    (output/'meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    lock_payload = (json.dumps({'purpose':'Frozen before continuation training; never train on this file',
                                **meta['files']['test.jsonl']},indent=2)+'\n').encode()
    lock = output/'TEST_LOCK.json'
    if not lock.exists() or lock.read_bytes() != lock_payload:
        lock.write_bytes(lock_payload)
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('--seed',type=int,default=SEED)
    parser.add_argument('--train-size',type=int,default=16000)
    parser.add_argument('--validation-size',type=int,default=1000)
    parser.add_argument('--test-size',type=int,default=100)
    parser.add_argument('--v1-data-dir',type=Path,default=OLD_DATA,
                        help='Original V1 train.jsonl and validation.jsonl for leakage exclusions')
    args = parser.parse_args()
    try:
        meta = write(args.output_dir,args.seed,args.train_size,args.validation_size,args.test_size,args.v1_data_dir)
    except (OSError,ValueError) as error:
        parser.exit(1,f'curriculum-v2: {error}\n')
    print(json.dumps(meta,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
