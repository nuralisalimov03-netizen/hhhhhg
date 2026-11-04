#!/usr/bin/env python3
"""Compare a frozen own checkpoint with the real pinned llama.cpp host engine.

Requires the training dependencies. It never changes weights or substitutes a
test model. GGUF F16 rounding is applied to 2-D reference tensors, as in export.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--tokenizer', type=Path, required=True)
    parser.add_argument('--runner', type=Path, default=Path('/workspace/.chits-tools/weqrom-ai-host/weqrom-ai-run'))
    parser.add_argument('--report', type=Path)
    parser.add_argument('--max-error', type=float, default=0.02)
    args = parser.parse_args()

    import sentencepiece as spm
    import numpy as np
    import torch
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'training'))
    from train import Config, SeedModel

    torch.set_num_threads(2)
    checkpoint = torch.load(args.checkpoint, map_location='cpu', weights_only=True)
    assert checkpoint.get('trained_from_scratch') is True
    assert checkpoint['tokenizer_sha256'] == digest(args.tokenizer)
    reference = SeedModel(Config(**checkpoint['config']))
    reference.load_state_dict({name: value.half().float() if value.ndim == 2 else value.float()
                               for name, value in checkpoint['model'].items()})
    reference.eval()
    tokenizer = spm.SentencePieceProcessor(model_file=str(args.tokenizer))
    prompts = [
        '<|im_start|>user\nСколько будет 2 + 2?<|im_end|>\n<|im_start|>assistant\n',
        '<|im_start|>user\nСделай HTML: <h1>Игра 🎮</h1>\n  две  строки\n<|im_end|>\n<|im_start|>assistant\n',
        '<|im_start|>user\nПривет!<|im_end|>\n<|im_start|>assistant\nПривет.<|im_end|>\n<|im_start|>user\nWhat is 3*7?\nhttps://example.test/a?q=ёж<|im_end|>\n<|im_start|>assistant\n',
    ]
    results = []
    with tempfile.TemporaryDirectory(prefix='weqrom-parity-') as directory:
        prompt_file = Path(directory) / 'prompt.txt'
        for number, prompt in enumerate(prompts):
            prompt_file.write_text(prompt, encoding='utf-8')
            command = [str(args.runner), '--model', str(args.model), '--prompt-file', str(prompt_file)]
            actual_ids = json.loads(subprocess.run(command + ['--tokenize'], capture_output=True, text=True, check=True).stdout)
            expected_ids = tokenizer.encode(prompt, out_type=int)
            assert actual_ids == expected_ids, f'Tokenizer IDs differ for prompt {number}: {actual_ids} != {expected_ids}'
            native = np.asarray(json.loads(subprocess.run(command + ['--logits'], capture_output=True, text=True, check=True).stdout), dtype=np.float32)
            with torch.inference_mode():
                expected = reference(torch.tensor([expected_ids], dtype=torch.long))[0, -1].numpy()
            assert native.shape == expected.shape == (checkpoint['config']['vocab_size'],)
            assert np.isfinite(native).all() and np.isfinite(expected).all()
            error = np.abs(native - expected)
            row = {'prompt': number, 'tokens': len(expected_ids), 'token_ids_match': True,
                   'logits_compared': int(error.size), 'max_absolute_error': float(error.max()),
                   'mean_absolute_error': float(error.mean()), 'native_argmax': int(native.argmax()),
                   'reference_argmax': int(expected.argmax())}
            results.append(row)
            assert row['max_absolute_error'] <= args.max_error, row
            assert row['native_argmax'] == row['reference_argmax'], row
    report = {'status': 'passed', 'checkpoint_step': checkpoint['step'],
              'checkpoint_sha256': digest(args.checkpoint), 'gguf_sha256': digest(args.model),
              'tokenizer_sha256': digest(args.tokenizer), 'reference_rounding': '2-D weights F16 to F32; norms F32',
              'tests': results, 'android_runtime_tested': False}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
