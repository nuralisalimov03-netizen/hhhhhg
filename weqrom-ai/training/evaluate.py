#!/usr/bin/env python3
"""Generate held-out answers from the actual own checkpoint; never replace them.

Numeric accuracy counts only a stated final answer, not incidental numbers in
the generated explanation. Raw outputs remain in the report for inspection.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import re
import time

import sentencepiece as spm
import torch
from train import Config, SeedModel, digest, format_messages, read_rows


def numeric_answer(text):
    matches = re.findall(r'ответ\s*:\s*(?:x\s*=\s*)?(-?\d+(?:[.,]\d+)?)', text, re.IGNORECASE)
    if matches:
        return float(matches[-1].replace(',', '.'))
    if re.fullmatch(r'\s*(?:x\s*=\s*)?-?\d+(?:[.,]\d+)?[.!]?\s*', text):
        return float(re.search(r'-?\d+(?:[.,]\d+)?', text).group().replace(',', '.'))
    return None


def evaluate(args):
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    checkpoint = torch.load(args.checkpoint, map_location='cpu', weights_only=True)
    if not checkpoint.get('trained_from_scratch'):
        raise ValueError('Only the own scratch-trained checkpoint is accepted')
    if digest(args.tokenizer) != checkpoint['tokenizer_sha256']:
        raise ValueError('Wrong tokenizer for this training run')
    model = SeedModel(Config(**checkpoint['config']))
    model.load_state_dict(checkpoint['model'])
    model.eval()
    tokenizer = spm.SentencePieceProcessor(model_file=str(args.tokenizer))
    end = tokenizer.piece_to_id('<|im_end|>')
    rows = read_rows(args.validation)
    random.Random(8411).shuffle(rows)
    selected = []
    counts = Counter()
    for row in rows:
        if counts[row['category']] < args.per_category:
            selected.append(row)
            counts[row['category']] += 1
    answers = []
    began = time.monotonic()
    numeric_total = numeric_correct = 0
    for index, row in enumerate(selected):
        prompt = format_messages(row['messages'][:-1], pending=True)
        ids = tokenizer.encode(prompt, out_type=int)
        start = time.monotonic()
        generated = model.generate(ids, end, args.max_tokens)
        text = tokenizer.decode(generated)
        result = {'id': row['id'], 'category': row['category'],
                  'question': row['messages'][0]['content'],
                  'reference': row['messages'][-1]['content'], 'generated': text,
                  'output_tokens': len(generated), 'seconds': time.monotonic() - start}
        if row['task']['kind'] in ('arithmetic', 'linear'):
            numeric_total += 1
            expected = float(row['task']['result'])
            stated = numeric_answer(text)
            correct = stated is not None and stated == expected
            numeric_correct += int(correct)
            result.update({'expected_number': expected, 'stated_number': stated, 'numeric_correct': correct})
        answers.append(result)
        print(json.dumps({'completed': index + 1, 'total': len(selected), **result}, ensure_ascii=False), flush=True)
    report = {'name': 'Weqrom Seed 10M', 'trained_from_scratch': True,
              'checkpoint_sha256': digest(args.checkpoint), 'step': checkpoint['step'],
              'tokenizer_sha256': digest(args.tokenizer), 'validation_sha256': digest(args.validation),
              'parameters': sum(p.numel() for p in model.parameters()),
              'sampling': 'greedy', 'per_category_requested': args.per_category,
              'category_counts': dict(counts), 'numeric_correct': numeric_correct,
              'numeric_total': numeric_total,
              'numeric_accuracy': numeric_correct / numeric_total if numeric_total else None,
              'seconds': time.monotonic() - began,
              'limits': ['A small synthetic held-out sample is not a general reasoning benchmark.',
                         'Exact arithmetic scoring is separate from the deterministic math tool.',
                         'Generated explanations can contain errors even when the final number is correct.',
                         'Known concept answers with new wording do not test new factual knowledge.'],
              'answers': answers}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'answers'}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--tokenizer', type=Path, required=True)
    parser.add_argument('--validation', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--per-category', type=int, default=5)
    parser.add_argument('--max-tokens', type=int, default=96)
    parser.add_argument('--threads', type=int, default=2)
    evaluate(parser.parse_args())


if __name__ == '__main__':
    main()
