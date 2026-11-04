#!/usr/bin/env python3
"""Train Weqrom Seed from random weights and an original SentencePiece vocabulary.

No pretrained model or tokenizer is loaded. Checkpoints are local training output.
The architecture deliberately follows the supported Llama inference operations;
this is a small experimental model, not a new foundation-model breakthrough.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time

import numpy as np
import sentencepiece as spm
import torch
from torch import nn
import torch.nn.functional as F


@dataclass
class Config:
    vocab_size: int = 4096
    dim: int = 256
    hidden_dim: int = 768
    n_layers: int = 10
    n_heads: int = 4
    n_kv_heads: int = 4
    context_length: int = 2048
    rope_theta: float = 10000.0
    norm_eps: float = 1e-5


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def format_messages(messages, pending=False):
    result = ''
    for message in messages:
        role = message['role']
        if role not in ('user', 'assistant'):
            raise ValueError('Only user/assistant messages are part of this training format')
        text = message['content']
        if '<|im_start|>' in text or '<|im_end|>' in text:
            raise ValueError('Reserved training delimiter in content')
        result += '<|im_start|>' + role + '\n' + text + '<|im_end|>\n'
    if pending:
        result += '<|im_start|>assistant\n'
    return result


class RMSNorm(nn.Module):
    def __init__(self, dimension, epsilon):
        super().__init__()
        self.weight = nn.Parameter(torch.ones(dimension))
        self.epsilon = epsilon

    def forward(self, x):
        return x * torch.rsqrt(x.square().mean(-1, keepdim=True) + self.epsilon) * self.weight


class Attention(nn.Module):
    def __init__(self, config):
        super().__init__()
        if config.n_heads != config.n_kv_heads or config.dim % config.n_heads:
            raise ValueError('This small training implementation requires equal Q/KV heads')
        self.heads = config.n_heads
        self.width = config.dim // config.n_heads
        if self.width % 2:
            raise ValueError('Rotary head width must be even')
        self.wq = nn.Linear(config.dim, config.dim, bias=False)
        self.wk = nn.Linear(config.dim, config.dim, bias=False)
        self.wv = nn.Linear(config.dim, config.dim, bias=False)
        self.wo = nn.Linear(config.dim, config.dim, bias=False)
        inverse = 1.0 / config.rope_theta ** (torch.arange(0, self.width, 2).float() / self.width)
        angles = torch.outer(torch.arange(config.context_length).float(), inverse)
        self.register_buffer('rope_cos', angles.cos(), persistent=False)
        self.register_buffer('rope_sin', angles.sin(), persistent=False)

    def rotate(self, tensor):
        # llama.cpp LLM_ARCH_LLAMA uses consecutive even/odd pairs. Unlike the
        # HF half-split convention, these Q/K weights need no export permutation.
        size = tensor.shape[1]
        pair = tensor.reshape(*tensor.shape[:-1], self.width // 2, 2)
        even, odd = pair[..., 0], pair[..., 1]
        cos = self.rope_cos[:size][None, :, None, :]
        sin = self.rope_sin[:size][None, :, None, :]
        return torch.stack((even * cos - odd * sin, even * sin + odd * cos), dim=-1).flatten(-2)

    def forward(self, x):
        batch, length, width = x.shape
        shape = (batch, length, self.heads, self.width)
        q, k = self.rotate(self.wq(x).view(shape)), self.rotate(self.wk(x).view(shape))
        v = self.wv(x).view(shape)
        output = F.scaled_dot_product_attention(q.transpose(1, 2), k.transpose(1, 2),
                                                v.transpose(1, 2), is_causal=True)
        return self.wo(output.transpose(1, 2).contiguous().view(batch, length, width))


class FeedForward(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.w1 = nn.Linear(config.dim, config.hidden_dim, bias=False)
        self.w2 = nn.Linear(config.hidden_dim, config.dim, bias=False)
        self.w3 = nn.Linear(config.dim, config.hidden_dim, bias=False)

    def forward(self, x):
        return self.w2(F.silu(self.w1(x)) * self.w3(x))


class Block(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.attention_norm = RMSNorm(config.dim, config.norm_eps)
        self.ffn_norm = RMSNorm(config.dim, config.norm_eps)
        self.attention = Attention(config)
        self.feed_forward = FeedForward(config)

    def forward(self, x):
        x = x + self.attention(self.attention_norm(x))
        return x + self.feed_forward(self.ffn_norm(x))


class SeedModel(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.tok_embeddings = nn.Embedding(config.vocab_size, config.dim)
        self.layers = nn.ModuleList([Block(config) for _ in range(config.n_layers)])
        self.norm = RMSNorm(config.dim, config.norm_eps)
        self.apply(self.initialize)
        for name, parameter in self.named_parameters():
            if name.endswith(('attention.wo.weight', 'feed_forward.w2.weight')):
                nn.init.normal_(parameter, std=0.02 / math.sqrt(2 * config.n_layers))

    @staticmethod
    def initialize(module):
        if isinstance(module, (nn.Linear, nn.Embedding)):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def forward(self, tokens):
        hidden = self.tok_embeddings(tokens)
        for layer in self.layers:
            hidden = layer(hidden)
        return F.linear(self.norm(hidden), self.tok_embeddings.weight)

    @torch.inference_mode()
    def generate(self, ids, eos, maximum=128):
        self.eval()
        values = list(ids)
        for _ in range(maximum):
            if len(values) >= self.config.context_length:
                break
            logits = self(torch.tensor([values], dtype=torch.long))
            token = int(logits[0, -1].argmax())
            if token == eos:
                break
            values.append(token)
        return values[len(ids):]


def read_rows(path):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError('No training examples')
    return rows


def prepare_tokenizer(rows, output, vocab_size):
    corpus = output / 'tokenizer-corpus.txt'
    corpus.write_text('\n'.join(format_messages(row['messages']) for row in rows), encoding='utf-8')
    target = output / 'tokenizer.model'
    if not target.exists():
        spm.SentencePieceTrainer.train(input=str(corpus), model_prefix=str(output / 'tokenizer'),
            vocab_size=vocab_size, model_type='bpe', byte_fallback=True,
            normalization_rule_name='identity', add_dummy_prefix=False,
            remove_extra_whitespaces=False, character_coverage=1.0,
            bos_id=1, eos_id=2, unk_id=0, pad_id=3,
            user_defined_symbols=['<|im_start|>', '<|im_end|>'],
            shuffle_input_sentence=False, num_threads=4, hard_vocab_limit=True,
            max_sentence_length=16384, minloglevel=1)
    tokenizer = spm.SentencePieceProcessor(model_file=str(target))
    if tokenizer.vocab_size() != vocab_size:
        raise ValueError('Cached own vocabulary has a different size')
    if tokenizer.piece_to_id('<|im_end|>') == tokenizer.unk_id():
        raise ValueError('Missing assistant-end token')
    return tokenizer


def encode_rows(rows, tokenizer, seed, authored_repeat=1, code_repeat=1):
    order = []
    for row in rows:
        count = code_repeat if row['category'] == 'code_example' else (
            1 if row['category'] in ('arithmetic', 'linear_equation') else authored_repeat)
        order.extend([row] * count)
    random.Random(seed).shuffle(order)
    tokens, weights = [], []
    for row in order:
        for message in row['messages']:
            encoded = tokenizer.encode(format_messages([message]), out_type=int)
            tokens.extend(encoded)
            weights.extend([1.0 if message['role'] == 'assistant' else 0.3] * len(encoded))
    return torch.tensor(tokens, dtype=torch.long), torch.tensor(weights, dtype=torch.float32)


def batch_from(data, masks, batch_size, length, generator):
    starts = torch.randint(0, len(data) - length - 1, (batch_size,), generator=generator)
    x = torch.stack([data[int(i):int(i)+length] for i in starts])
    y = torch.stack([data[int(i)+1:int(i)+length+1] for i in starts])
    weight = torch.stack([masks[int(i)+1:int(i)+length+1] for i in starts])
    return x, y, weight


def weighted_loss(logits, targets, weights):
    raw = F.cross_entropy(logits.flatten(0, 1), targets.flatten(), reduction='none').view_as(weights)
    return (raw * weights).sum() / weights.sum()


def atomic_save(value, target):
    temporary = target.with_suffix('.partial')
    torch.save(value, temporary)
    temporary.replace(target)


def train(args):
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    rows = read_rows(args.data_dir / 'train.jsonl')
    heldout = read_rows(args.data_dir / 'validation.jsonl')
    config = Config()
    tokenizer = prepare_tokenizer(rows, output, config.vocab_size)
    train_tokens, train_weights = encode_rows(rows, tokenizer, args.seed, args.authored_repeat, args.code_repeat)
    validation_tokens, validation_weights = encode_rows(heldout, tokenizer, args.seed + 1)
    model = SeedModel(config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, betas=(0.9, 0.95), weight_decay=0.01)
    start_step = 0
    previous_seconds = 0.0
    best_validation = float('inf')
    generator = torch.Generator().manual_seed(args.seed + 2)
    if args.resume:
        checkpoint = torch.load(args.resume, map_location='cpu', weights_only=True)
        if not checkpoint.get('trained_from_scratch') or checkpoint['config'] != asdict(config):
            raise ValueError('Resume only an own matching scratch-trained checkpoint')
        if checkpoint['tokenizer_sha256'] != digest(output / 'tokenizer.model'):
            raise ValueError('Resume vocabulary mismatch')
        model.load_state_dict(checkpoint['model'])
        optimizer.load_state_dict(checkpoint['optimizer'])
        start_step = checkpoint['step']
        previous_seconds = checkpoint['training_seconds']
        best_validation = checkpoint['best_validation']
        if args.restart_schedule:
            best_validation = float('inf')
        if 'random_state' in checkpoint:
            generator.set_state(checkpoint['random_state'])
    parameters = sum(value.numel() for value in model.parameters())
    info = {'name': 'Weqrom Seed 10M', 'parameters': parameters, 'config': asdict(config),
            'seed': args.seed, 'trained_from_scratch': True, 'pretrained_weights': False,
            'pretrained_tokenizer': False, 'torch_version': str(torch.__version__),
            'train_sha256': digest(args.data_dir / 'train.jsonl'),
            'validation_sha256': digest(args.data_dir / 'validation.jsonl'),
            'tokenizer_sha256': digest(output / 'tokenizer.model'),
            'train_examples': len(rows), 'validation_examples': len(heldout),
            'train_tokens': len(train_tokens), 'validation_tokens': len(validation_tokens),
            'sequence_length': args.sequence_length, 'batch_size': args.batch_size,
            'effective_tokens_per_step': args.sequence_length * args.batch_size,
            'training_context_limit': args.sequence_length}
    info['curriculum_repeats'] = {'authored': args.authored_repeat, 'code': args.code_repeat, 'numeric': 1}
    if args.resume:
        info['resume_lineage'] = {'checkpoint_sha256': digest(args.resume), 'step': start_step,
                                  'previous_provenance': checkpoint['provenance']}
    (output / 'provenance.json').write_text(json.dumps(info, ensure_ascii=False, indent=2))
    print(json.dumps({'event': 'begin', **info}), flush=True)
    began = time.monotonic()
    model.train()
    latest_step = start_step
    losses = []

    def checkpoint(step, validation):
        value = {'model': model.state_dict(), 'optimizer': optimizer.state_dict(),
                 'config': asdict(config), 'trained_from_scratch': True,
                 'tokenizer_sha256': info['tokenizer_sha256'], 'step': step,
                 'training_seconds': previous_seconds + time.monotonic() - began,
                 'best_validation': best_validation, 'validation_loss': validation,
                 'random_state': generator.get_state(), 'provenance': info}
        atomic_save(value, output / 'latest.pt')
        return value

    logfile = (output / 'progress.jsonl').open('a', encoding='utf-8')
    try:
        for step in range(start_step + 1, args.steps + 1):
            latest_step = step
            elapsed_step = time.monotonic()
            schedule_step = step - start_step if args.restart_schedule else step
            schedule_total = args.steps - start_step if args.restart_schedule else args.steps
            warmup = min(1.0, schedule_step / 30)
            decay = 0.2 + 0.8 * 0.5 * (1 + math.cos(math.pi * min(schedule_step, schedule_total) / schedule_total))
            rate = args.learning_rate * warmup * decay
            for group in optimizer.param_groups:
                group['lr'] = rate
            x, y, weight = batch_from(train_tokens, train_weights, args.batch_size, args.sequence_length, generator)
            optimizer.zero_grad(set_to_none=True)
            loss = weighted_loss(model(x), y, weight)
            if not torch.isfinite(loss):
                raise FloatingPointError('Non-finite training loss; do not publish these weights')
            loss.backward()
            gradient = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(float(loss.detach()))
            if step % args.log_every == 0 or step == start_step + 1:
                now = time.monotonic()
                item = {'step': step, 'loss': sum(losses) / len(losses), 'lr': rate,
                        'seconds': previous_seconds + now - began,
                        'step_seconds': now - elapsed_step,
                        'tokens_per_second': (step - start_step) * args.batch_size * args.sequence_length / (now - began),
                        'gradient_norm': float(gradient)}
                losses.clear()
                line = json.dumps(item)
                print(line, flush=True)
                logfile.write(line + '\n'); logfile.flush()
            if step % args.eval_every == 0 or step == args.steps:
                model.eval()
                evaluation = torch.Generator().manual_seed(args.seed + 100)
                with torch.no_grad():
                    values = []
                    for _ in range(args.eval_batches):
                        vx, vy, vw = batch_from(validation_tokens, validation_weights, args.batch_size, args.sequence_length, evaluation)
                        values.append(float(weighted_loss(model(vx), vy, vw)))
                validation = sum(values) / len(values)
                improved = validation < best_validation
                best_validation = min(validation, best_validation)
                value = checkpoint(step, validation)
                if improved:
                    atomic_save(value, output / 'best.pt')
                print(json.dumps({'event': 'validation', 'step': step, 'loss': validation,
                                  'best': best_validation}), flush=True)
                model.train()
            elif step % args.save_every == 0:
                checkpoint(step, None)
            if args.max_seconds and time.monotonic() - began >= args.max_seconds:
                print(json.dumps({'event': 'time_budget_reached', 'step': step}), flush=True)
                break
        checkpoint(latest_step, None)
    finally:
        logfile.close()
    info.update({'steps': latest_step, 'seen_tokens': latest_step * args.batch_size * args.sequence_length,
                 'training_seconds': previous_seconds + time.monotonic() - began,
                 'best_validation_loss': best_validation,
                 'best_checkpoint_sha256': digest(output / 'best.pt') if (output / 'best.pt').exists() else None})
    (output / 'training-report.json').write_text(json.dumps(info, ensure_ascii=False, indent=2))
    print(json.dumps({'event': 'finished', **info}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--steps', type=int, default=1000)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--sequence-length', type=int, default=192)
    parser.add_argument('--learning-rate', type=float, default=0.002)
    parser.add_argument('--seed', type=int, default=20261104)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--log-every', type=int, default=20)
    parser.add_argument('--eval-every', type=int, default=100)
    parser.add_argument('--eval-batches', type=int, default=8)
    parser.add_argument('--save-every', type=int, default=100)
    parser.add_argument('--max-seconds', type=int, default=0)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--authored-repeat', type=int, default=1)
    parser.add_argument('--code-repeat', type=int, default=1)
    parser.add_argument('--restart-schedule', action='store_true')
    args = parser.parse_args()
    if not (1 <= args.batch_size <= 64 and 32 <= args.sequence_length <= 512 and 1 <= args.threads <= 4
            and 1 <= args.authored_repeat <= 16 and 1 <= args.code_repeat <= 16):
        parser.error('Unsupported CPU training batch/context/thread setting')
    train(args)


if __name__ == '__main__':
    main()
