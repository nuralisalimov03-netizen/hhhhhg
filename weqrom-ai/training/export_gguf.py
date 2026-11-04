#!/usr/bin/env python3
"""Export this project's own trained SeedModel checkpoint to llama.cpp GGUF.

No Hugging Face model, tokenizer, converter or weights are used. The training
implementation already applies RoPE to adjacent even/odd coordinates, as does
llama.cpp's normal Llama graph, so its Q/K matrices must NOT be HF-permuted.

Dependencies: torch, numpy, sentencepiece, protobuf and the local, unmodified
gguf-py from pinned llama.cpp b6500. Nothing is installed or downloaded here.
The default source checkout is $CHITS_TOOLS_DIR/llama.cpp (normally
/workspace/.chits-tools/llama.cpp). Use --llama-dir to select another checkout
of that exact revision. --help works without these optional dependencies.

The output manifest records provenance and integrity, not model quality. Real
tokenizer/logit parity and inference must be tested before distributing a model.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


LLAMA_COMMIT = "a7a98e0fffed794396b3fbad4dcdbbc184963645"
DEFAULT_NAME = "Weqrom Seed 10M"
CHAT_TEMPLATE = (
    "{% for message in messages %}"
    "{{ '<|im_start|>' + message['role'] + '\\n' + message['content'] + '<|im_end|>\\n' }}"
    "{% endfor %}"
    "{% if add_generation_prompt %}{{ '<|im_start|>assistant\\n' }}{% endif %}"
)
CONFIG_KEYS = {
    "vocab_size", "dim", "hidden_dim", "n_layers", "n_heads", "n_kv_heads",
    "context_length", "rope_theta", "norm_eps",
}


class ExportError(ValueError):
    """Invalid checkpoint, tokenizer or output configuration."""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _regular_input(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ExportError(f"{label} must be a local regular file: {path}")
    return path.resolve()


def _output_path(path: Path, sources: set[Path]) -> Path:
    if path.is_symlink():
        raise ExportError(f"Refusing a symlink output: {path}")
    resolved = path.resolve()
    if resolved in sources:
        raise ExportError("An output would overwrite a checkpoint or tokenizer")
    if resolved.exists() and not resolved.is_file():
        raise ExportError(f"Output must be a regular file: {path}")
    return resolved


def _dependencies(llama_dir: Path) -> tuple[Any, Any, Any, Any, Any]:
    try:
        actual = subprocess.run(
            ["git", "-C", str(llama_dir), "rev-parse", "HEAD"],
            check=True, text=True, capture_output=True,
        ).stdout.strip()
        if actual != LLAMA_COMMIT:
            raise ExportError(f"llama.cpp must be pinned to {LLAMA_COMMIT}")
        clean = subprocess.run(
            ["git", "-C", str(llama_dir), "diff", "--quiet", "HEAD", "--", "gguf-py"],
            check=False, capture_output=True,
        )
        if clean.returncode != 0:
            raise ExportError("Pinned gguf-py has tracked changes; use a clean checkout")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ExportError("Provide a local llama.cpp b6500 checkout with --llama-dir") from exc

    package_root = (llama_dir / "gguf-py").resolve()
    if not (package_root / "gguf" / "gguf_writer.py").is_file():
        raise ExportError("Pinned checkout is missing gguf-py")
    try:
        import numpy as np
        import torch
        import sentencepiece as spm
        from sentencepiece import sentencepiece_model_pb2 as spm_proto
        if "gguf" in sys.modules:
            cached = Path(sys.modules["gguf"].__file__).resolve()
            if not cached.is_relative_to(package_root):
                raise ExportError("A different gguf package was already imported")
        sys.path.insert(0, str(package_root))
        gguf = importlib.import_module("gguf")
    except ImportError as exc:
        raise ExportError(
            "Missing export dependency; install the project's pinned torch, numpy, "
            "sentencepiece and protobuf requirements first"
        ) from exc
    return torch, np, spm, spm_proto, gguf


def _validate_config(config: Any) -> dict:
    if not isinstance(config, dict) or set(config) != CONFIG_KEYS:
        raise ExportError("Checkpoint config does not match the own SeedModel format")
    for key in CONFIG_KEYS - {"rope_theta", "norm_eps"}:
        if type(config[key]) is not int or config[key] <= 0:
            raise ExportError(f"Config {key} must be a positive integer")
    for key in ("rope_theta", "norm_eps"):
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ExportError(f"Config {key} must be finite and positive")
    if config["n_heads"] != config["n_kv_heads"]:
        raise ExportError("This own training implementation requires equal Q/KV heads")
    if config["dim"] % config["n_heads"] or (config["dim"] // config["n_heads"]) % 2:
        raise ExportError("Attention heads need an even integral rotary dimension")
    if config["vocab_size"] < 262:
        raise ExportError("Vocabulary cannot contain required byte and special tokens")
    return config


def _tensor_shapes(config: dict) -> dict[str, tuple[str, tuple[int, ...]]]:
    dim, hidden = config["dim"], config["hidden_dim"]
    mapping = {
        "tok_embeddings.weight": ("token_embd.weight", (config["vocab_size"], dim)),
        "norm.weight": ("output_norm.weight", (dim,)),
    }
    for layer in range(config["n_layers"]):
        source, target = f"layers.{layer}", f"blk.{layer}"
        mapping[f"{source}.attention_norm.weight"] = (f"{target}.attn_norm.weight", (dim,))
        mapping[f"{source}.ffn_norm.weight"] = (f"{target}.ffn_norm.weight", (dim,))
        for projection in ("q", "k", "v", "o"):
            mapped = "output" if projection == "o" else projection
            mapping[f"{source}.attention.w{projection}.weight"] = (
                f"{target}.attn_{mapped}.weight", (dim, dim),
            )
        for number, mapped, shape in (
            (1, "gate", (hidden, dim)),
            (2, "down", (dim, hidden)),
            (3, "up", (hidden, dim)),
        ):
            mapping[f"{source}.feed_forward.w{number}.weight"] = (
                f"{target}.ffn_{mapped}.weight", shape,
            )
    return mapping


def _read_tokenizer(path: Path, config: dict, spm: Any, spm_proto: Any, gguf: Any) -> tuple[list, list, list]:
    processor = spm.SentencePieceProcessor(model_file=str(path))
    proto = spm_proto.ModelProto()
    proto.ParseFromString(path.read_bytes())
    normalizer = proto.normalizer_spec
    if proto.trainer_spec.model_type != spm_proto.TrainerSpec.BPE or not proto.trainer_spec.byte_fallback:
        raise ExportError("Only the own SentencePiece BPE with byte fallback is supported")
    if (
        normalizer.name != "identity" or normalizer.precompiled_charsmap
        or normalizer.add_dummy_prefix or normalizer.remove_extra_whitespaces
        or not normalizer.escape_whitespaces
    ):
        raise ExportError("Tokenizer must preserve whitespace with identity normalization and no dummy prefix")
    if proto.HasField("denormalizer_spec") and proto.denormalizer_spec.precompiled_charsmap:
        raise ExportError("Custom tokenizer denormalization is unsupported")
    if processor.vocab_size() != config["vocab_size"] or len(proto.pieces) != config["vocab_size"]:
        raise ExportError("Tokenizer vocabulary size does not match checkpoint embeddings")
    expected = {0: "<unk>", 1: "<s>", 2: "</s>", 3: "<pad>", 4: "<|im_start|>", 5: "<|im_end|>"}
    for token_id, text in expected.items():
        if processor.id_to_piece(token_id) != text:
            raise ExportError(f"Tokenizer must assign {text!r} to ID {token_id}")
    if (processor.unk_id(), processor.bos_id(), processor.eos_id(), processor.pad_id()) != (0, 1, 2, 3):
        raise ExportError("SentencePiece special token IDs do not match training")
    if processor.encode("<|im_start|>", out_type=int) != [4] or processor.encode("<|im_end|>", out_type=int) != [5]:
        raise ExportError("Chat delimiters must be atomic tokenizer pieces")

    tokens, scores, types = [], [], []
    bytes_seen = set()
    for token_id in range(processor.vocab_size()):
        text = processor.id_to_piece(token_id)
        score = processor.get_score(token_id)
        if not text or not math.isfinite(score):
            raise ExportError("Tokenizer contains an empty piece or non-finite score")
        if processor.is_unknown(token_id):
            token_type = gguf.TokenType.UNKNOWN
        elif token_id in (4, 5) or processor.is_control(token_id):
            token_type = gguf.TokenType.CONTROL
        elif processor.is_unused(token_id):
            token_type = gguf.TokenType.UNUSED
        elif processor.is_byte(token_id):
            token_type = gguf.TokenType.BYTE
            bytes_seen.add(text)
        else:
            token_type = gguf.TokenType.NORMAL
        tokens.append(text.encode("utf-8"))
        scores.append(float(score))
        types.append(token_type)
    if bytes_seen != {f"<0x{byte:02X}>" for byte in range(256)}:
        raise ExportError("Tokenizer must contain all 256 byte-fallback pieces")
    return tokens, scores, types


def _metadata(writer: Any, config: dict, name: str, tokenizer_data: tuple, gguf: Any, step: int) -> None:
    writer.add_name(name)
    writer.add_description("Experimental own small model trained from random initialization on a local synthetic corpus")
    writer.add_file_type(int(gguf.LlamaFileType.MOSTLY_F16))
    writer.add_context_length(config["context_length"])
    writer.add_embedding_length(config["dim"])
    writer.add_block_count(config["n_layers"])
    writer.add_feed_forward_length(config["hidden_dim"])
    writer.add_head_count(config["n_heads"])
    writer.add_head_count_kv(config["n_kv_heads"])
    writer.add_layer_norm_rms_eps(float(config["norm_eps"]))
    writer.add_rope_dimension_count(config["dim"] // config["n_heads"])
    writer.add_rope_freq_base(float(config["rope_theta"]))
    writer.add_vocab_size(config["vocab_size"])
    writer.add_tokenizer_model("llama")
    writer.add_tokenizer_pre("default")
    tokens, scores, types = tokenizer_data
    writer.add_token_list(tokens)
    writer.add_token_scores(scores)
    writer.add_token_types(types)
    writer.add_bos_token_id(1)
    writer.add_eos_token_id(5)
    writer.add_eot_token_id(5)
    writer.add_unk_token_id(0)
    writer.add_pad_token_id(3)
    writer.add_add_bos_token(False)
    writer.add_add_eos_token(False)
    writer.add_add_space_prefix(False)
    writer.add_remove_extra_whitespaces(False)
    writer.add_chat_template(CHAT_TEMPLATE)
    writer.add_bool("weqrom.trained_from_scratch", True)
    writer.add_bool("weqrom.pretrained_weights", False)
    writer.add_uint64("weqrom.training_step", step)


def export_gguf(
    checkpoint: Path | str, tokenizer: Path | str, output: Path | str,
    manifest: Path | str, *, name: str = DEFAULT_NAME, llama_dir: Path | str | None = None,
) -> dict:
    """Validate and export a real own-training checkpoint, returning its manifest.

    Files are staged beside their destinations, then replaced only after all
    validation and GGUF writing succeeds. Existing inputs are never overwritten.
    The two output files are individually atomic, not a transactional pair.
    """
    if not isinstance(name, str) or not name.strip() or len(name.encode("utf-8")) > 256 or any(ord(c) < 32 for c in name):
        raise ExportError("Model name must be a nonempty, single-line name of at most 256 UTF-8 bytes")
    checkpoint_path = _regular_input(Path(checkpoint), "Checkpoint")
    tokenizer_path = _regular_input(Path(tokenizer), "Tokenizer")
    sources = {checkpoint_path, tokenizer_path}
    output_path = _output_path(Path(output), sources)
    manifest_path = _output_path(Path(manifest), sources)
    if output_path == manifest_path:
        raise ExportError("GGUF and manifest need different output paths")
    selected_llama = Path(llama_dir) if llama_dir is not None else Path(
        os.environ.get("CHITS_TOOLS_DIR", "/workspace/.chits-tools")
    ) / "llama.cpp"
    torch, np, spm, spm_proto, gguf = _dependencies(selected_llama)
    checkpoint_hash, tokenizer_hash = digest(checkpoint_path), digest(tokenizer_path)
    try:
        saved = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    except Exception as exc:
        raise ExportError("Could not safely load own torch checkpoint with weights_only=True") from exc
    if not isinstance(saved, dict) or saved.get("trained_from_scratch") is not True:
        raise ExportError("Checkpoint lacks explicit own-training provenance")
    if saved.get("tokenizer_sha256") != tokenizer_hash:
        raise ExportError("Checkpoint tokenizer SHA-256 does not match the supplied tokenizer")
    step = saved.get("step")
    if type(step) is not int or step <= 0:
        raise ExportError("Checkpoint must record at least one completed training step")
    provenance = saved.get("provenance")
    if not isinstance(provenance, dict) or (
        provenance.get("trained_from_scratch") is not True
        or provenance.get("pretrained_weights") is not False
        or provenance.get("pretrained_tokenizer") is not False
    ):
        raise ExportError("Checkpoint provenance must explicitly exclude pretrained weights and tokenizer")
    config = _validate_config(saved.get("config"))
    mapping = _tensor_shapes(config)
    state = saved.get("model")
    if not isinstance(state, dict) or set(state) != set(mapping):
        missing = sorted(set(mapping) - set(state or {})) if isinstance(state, dict) else sorted(mapping)
        unexpected = sorted(set(state) - set(mapping)) if isinstance(state, dict) else []
        raise ExportError(f"Checkpoint tensor keys differ: missing={missing}, unexpected={unexpected}")
    arrays = []
    parameters = 0
    for source, (target, shape) in mapping.items():
        tensor = state[source]
        if not isinstance(tensor, torch.Tensor) or tuple(tensor.shape) != shape:
            raise ExportError(f"Tensor {source} must have shape {shape}")
        if tensor.layout != torch.strided or tensor.dtype not in (torch.float16, torch.float32, torch.bfloat16):
            raise ExportError(f"Tensor {source} has an unsupported layout or dtype")
        if not bool(torch.isfinite(tensor).all()):
            raise ExportError(f"Tensor {source} contains non-finite values")
        dtype = torch.float32 if len(shape) == 1 else torch.float16
        converted = tensor.detach().to(dtype=dtype).contiguous().numpy()
        if not bool(np.isfinite(converted).all()):
            raise ExportError(f"Tensor {source} overflows its exported precision")
        arrays.append((target, converted))
        parameters += tensor.numel()
    tokenizer_data = _read_tokenizer(tokenizer_path, config, spm, spm_proto, gguf)
    if digest(checkpoint_path) != checkpoint_hash or digest(tokenizer_path) != tokenizer_hash:
        raise ExportError("An input file changed during export; retry with a fixed checkpoint")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    staged_model: Path | None = None
    staged_manifest: Path | None = None
    writer = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent, delete=False) as stream:
            staged_model = Path(stream.name)
        writer = gguf.GGUFWriter(path=staged_model, arch="llama")
        _metadata(writer, config, name, tokenizer_data, gguf, step)
        for target, array in arrays:
            writer.add_tensor(target, array)
        writer.write_header_to_file()
        writer.write_kv_data_to_file()
        writer.write_tensors_to_file()
        writer.close()
        writer = None
        with staged_model.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                raise ExportError("GGUF writer did not produce a valid container header")
            os.fsync(stream.fileno())
        result = {
            "schema_version": 1,
            "name": name,
            "sha256": digest(staged_model),
            "bytes": staged_model.stat().st_size,
            "parameters": parameters,
            "trained_from_scratch": True,
            "pretrained_weights": False,
            "pretrained_tokenizer": False,
            "architecture": "llama",
            "precision": "F16 matrices / F32 norms",
            "checkpoint_sha256": checkpoint_hash,
            "tokenizer_sha256": tokenizer_hash,
            "training_step": step,
            "llama_cpp_commit": LLAMA_COMMIT,
            "config": config,
            "validation": {"native_inference": "not_checked_by_exporter", "model_quality": "not_measured_by_exporter"},
        }
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=f".{manifest_path.name}.", suffix=".tmp", dir=manifest_path.parent, delete=False) as stream:
            staged_manifest = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged_model, output_path)
        staged_model = None
        os.replace(staged_manifest, manifest_path)
        staged_manifest = None
        return result
    finally:
        if writer is not None:
            writer.close()
        for staged in (staged_model, staged_manifest):
            if staged is not None:
                staged.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", type=Path, required=True, help="Own trained .pt checkpoint")
    parser.add_argument("--tokenizer", type=Path, required=True, help="Its own SentencePiece tokenizer.model")
    parser.add_argument("--output", type=Path, required=True, help="Destination .gguf file")
    parser.add_argument("--manifest", type=Path, required=True, help="Destination integrity/provenance JSON")
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--llama-dir", type=Path, help="Clean pinned llama.cpp b6500 source checkout")
    args = parser.parse_args(argv)
    try:
        result = export_gguf(args.checkpoint, args.tokenizer, args.output, args.manifest, name=args.name, llama_dir=args.llama_dir)
    except (ExportError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Export failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
