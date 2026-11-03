#!/usr/bin/env python3
"""Build verified, sharded APK model assets from existing local GGUF files.

This command does not download weights or prove that a GGUF is a usable model.
It checks the GGUF magic and packages byte-for-byte data with SHA-256 metadata;
Android's BundledModels additionally validates actual weights before use.

Examples:
  python3 tools/bundle-models.py --text /private/qwen-fast.gguf
  python3 tools/bundle-models.py --text /private/qwen-fast.gguf \
    --vision /private/qwen-vl.gguf --projector /private/mmproj.gguf \
    --vision-revision 0123456789abcdef0123456789abcdef01234567 --clear

An existing nonempty output is replaced only with --clear and only if its
generated manifest and every part match. Unknown files and symlinks are refused.
Use --clear without model arguments to remove only an owned generated bundle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


GENERATOR = "chits-market.bundle-models/v1"
DEFAULT_SHARD_SIZE = 64 * 1024 * 1024
TARGETS = {
    "text": "assistant.gguf",
    "vision": "assistant-vision.gguf",
    "projector": "assistant-mmproj.gguf",
}
SHA256 = re.compile(r"[0-9a-f]{64}")
REVISION = re.compile(r"[0-9a-f]{40}")
PROJECT = Path(__file__).resolve().parents[1]


class BundleError(ValueError):
    pass


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def _output_path(raw: Path) -> Path:
    output = Path(os.path.abspath(raw))
    if output.name != "models":
        raise BundleError("Output directory must be named models")
    for candidate in (output, *output.parents):
        if candidate.is_symlink():
            raise BundleError("Output directory and its parents must not be symlinks")
    if output.exists() and not output.is_dir():
        raise BundleError("Output is not a directory")
    return output


def _manifest_paths(manifest: dict) -> dict[str, tuple[int, str]]:
    if not isinstance(manifest, dict) or not _integer(manifest.get("schema")) or manifest.get("schema") != 1 or manifest.get("generator") != GENERATOR:
        raise BundleError("Output does not have an owned bundle manifest")
    if set(manifest) - {"schema", "generator", "files", "visionRevision"}:
        raise BundleError("Unexpected fields in the existing bundle manifest")
    files = manifest.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 3:
        raise BundleError("Invalid bundle file list")
    paths = {}
    roles = set()
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"role", "target", "size", "sha256", "parts"}:
            raise BundleError("Invalid bundle entry")
        role = entry.get("role")
        if not isinstance(role, str) or role not in TARGETS or role in roles or entry.get("target") != TARGETS[role]:
            raise BundleError("Invalid or duplicated bundle role")
        roles.add(role)
        if not _integer(entry.get("size")) or entry["size"] < 8 or not isinstance(entry.get("sha256"), str) or not SHA256.fullmatch(entry["sha256"]):
            raise BundleError("Invalid model size or hash")
        parts = entry.get("parts")
        if not isinstance(parts, list) or not 1 <= len(parts) <= 65536:
            raise BundleError("Invalid bundle part list")
        size = 0
        for index, part in enumerate(parts):
            expected = f"models/{role}-{index:03d}.bin"
            if not isinstance(part, dict) or set(part) != {"path", "size", "sha256"} or part.get("path") != expected:
                raise BundleError("Unexpected bundle part path")
            if not _integer(part.get("size")) or not 1 <= part["size"] <= DEFAULT_SHARD_SIZE:
                raise BundleError("Invalid bundle part size")
            if not isinstance(part.get("sha256"), str) or not SHA256.fullmatch(part["sha256"]):
                raise BundleError("Invalid bundle part hash")
            filename = Path(expected).name
            if filename in paths:
                raise BundleError("Duplicated bundle part")
            paths[filename] = (part["size"], part["sha256"])
            size += part["size"]
        if size != entry["size"]:
            raise BundleError("Bundle part sizes do not add up")
    if ("vision" in roles) != ("projector" in roles):
        raise BundleError("Vision bundles need both model and projector")
    if "vision" in roles:
        if not isinstance(manifest.get("visionRevision"), str) or not REVISION.fullmatch(manifest["visionRevision"]):
            raise BundleError("Vision bundle revision is not pinned")
    elif "visionRevision" in manifest:
        raise BundleError("Unexpected vision revision for a text-only bundle")
    return paths


def _inspect_output(output: Path, allow_nonempty: bool):
    """Return a fingerprint only after verifying that any contents are owned."""
    if not output.exists():
        return None
    if output.is_symlink() or not output.is_dir():
        raise BundleError("Output must be a regular directory")
    children = list(output.iterdir())
    if not children:
        info = output.stat()
        return (info.st_dev, info.st_ino, ())
    if not allow_nonempty:
        raise BundleError("Output already contains files; use --clear to replace an owned bundle")
    if any(path.is_symlink() or not path.is_file() for path in children):
        raise BundleError("Refusing output containing directories or symlinks")
    manifest_path = output / "manifest.json"
    if not manifest_path.is_file() or manifest_path.stat().st_size > 4 * 1024 * 1024:
        raise BundleError("Output does not contain a bounded generated manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError) as error:
        raise BundleError("Cannot read the existing bundle manifest") from error
    expected = _manifest_paths(manifest)
    if {p.name for p in children} != set(expected) | {"manifest.json"}:
        raise BundleError("Unknown files in models directory; no files were removed")
    for name, (size, digest) in expected.items():
        path = output / name
        if path.stat().st_size != size or _sha256(path) != digest:
            raise BundleError("Existing bundle part has changed; no files were removed")
    # Also verify assembled hashes, so an inconsistent hand-edited manifest is refused.
    for entry in manifest["files"]:
        digest = hashlib.sha256()
        for part in entry["parts"]:
            with (output / Path(part["path"]).name).open("rb") as stream:
                for data in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(data)
        if digest.hexdigest() != entry["sha256"]:
            raise BundleError("Existing model hash does not match its parts")
    info = output.stat()
    signature = tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino)
                      for p in sorted(children))
    return (info.st_dev, info.st_ino, signature)


def _remove_owned(output: Path):
    _inspect_output(output, True)
    # This is deliberately not recursive: only validated flat generated files go.
    for path in output.iterdir():
        path.unlink()
    output.rmdir()


def _validate_source(raw: Path, output: Path) -> Path:
    path = Path(os.path.abspath(raw))
    if path.is_symlink():
        raise BundleError("Model source must not be a symlink")
    try:
        canonical = path.resolve(strict=True)
    except OSError as error:
        raise BundleError("Model source is missing") from error
    if canonical == output or output in canonical.parents:
        raise BundleError("Model source must be outside the output directory")
    if not canonical.is_file() or canonical.stat().st_size < 8:
        raise BundleError("Model source must be a nonempty GGUF file")
    with canonical.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise BundleError("Model source does not have GGUF magic")
    return canonical


def _write_model(role: str, source: Path, stage: Path, shard_size: int) -> dict:
    before = source.stat()
    overall = hashlib.sha256()
    parts = []
    total = 0
    with source.open("rb") as stream:
        while True:
            first = stream.read(min(shard_size, 1024 * 1024))
            if not first:
                break
            name = f"{role}-{len(parts):03d}.bin"
            digest = hashlib.sha256()
            size = 0
            with (stage / name).open("xb") as output:
                data = first
                while data:
                    output.write(data)
                    digest.update(data)
                    overall.update(data)
                    size += len(data)
                    total += len(data)
                    if size == shard_size:
                        break
                    data = stream.read(min(shard_size - size, 1024 * 1024))
                output.flush()
                os.fsync(output.fileno())
            parts.append({"path": f"models/{name}", "size": size, "sha256": digest.hexdigest()})
    after = source.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or total != before.st_size:
        raise BundleError("Model source changed while packaging")
    return {"role": role, "target": TARGETS[role], "size": total, "sha256": overall.hexdigest(), "parts": parts}


def bundle_models(*, output: Path, text: Path | None = None, vision: Path | None = None,
                  projector: Path | None = None, vision_revision: str | None = None,
                  shard_size: int = DEFAULT_SHARD_SIZE, clear: bool = False) -> dict | None:
    output = _output_path(Path(output))
    if not _integer(shard_size) or not 1 <= shard_size <= DEFAULT_SHARD_SIZE:
        raise BundleError("Shard size must be between 1 and 67108864 bytes")
    has_vision = any(value is not None for value in (vision, projector, vision_revision))
    if has_vision and not all(value is not None for value in (vision, projector, vision_revision)):
        raise BundleError("Pass --vision, --projector and --vision-revision together")
    if has_vision and (not isinstance(vision_revision, str) or not REVISION.fullmatch(vision_revision)):
        raise BundleError("Vision revision must be a lowercase 40-digit hexadecimal commit")
    sources = {role: _validate_source(path, output) for role, path in
               (("text", text), ("vision", vision), ("projector", projector)) if path is not None}
    if not sources and not clear:
        raise BundleError("Provide --text and/or a complete vision pair, or explicitly --clear")
    original = _inspect_output(output, clear)
    if not sources:
        if output.exists() and any(output.iterdir()):
            _remove_owned(output)
            output.mkdir()
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".models-stage-", dir=output.parent) as raw_stage:
        stage = Path(raw_stage)
        manifest = {"schema": 1, "generator": GENERATOR,
                    "files": [_write_model(role, source, stage, shard_size) for role, source in sources.items()]}
        if has_vision:
            manifest["visionRevision"] = vision_revision
        _manifest_paths(manifest)
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if original != _inspect_output(output, clear):
            raise BundleError("Output changed while packaging; no existing files were removed")
        backup = None
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix=".models-backup-", dir=output.parent))
            os.replace(output, backup)
        try:
            os.replace(stage, output)
        except BaseException:
            if backup is not None and not output.exists():
                os.replace(backup, output)
            raise
        if backup is not None:
            _remove_owned(backup)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--text", type=Path, help="Local fast-text GGUF; no download is performed")
    parser.add_argument("--vision", type=Path, help="Local vision language GGUF")
    parser.add_argument("--projector", type=Path, help="Matching local mmproj GGUF")
    parser.add_argument("--vision-revision", help="Immutable 40-character revision shared by the vision pair")
    parser.add_argument("--output", type=Path, default=PROJECT / "app/src/main/assets/models")
    parser.add_argument("--shard-size-bytes", type=int, default=DEFAULT_SHARD_SIZE)
    parser.add_argument("--clear", action="store_true", help="Replace or clear only a verified generated models directory")
    args = parser.parse_args(argv)
    try:
        manifest = bundle_models(output=args.output, text=args.text, vision=args.vision,
                                 projector=args.projector, vision_revision=args.vision_revision,
                                 shard_size=args.shard_size_bytes, clear=args.clear)
    except (BundleError, OSError) as error:
        parser.exit(1, "Cannot bundle models: " + str(error) + "\n")
    if manifest is None:
        print("Owned model assets cleared; no model manifest created")
    else:
        total = sum(entry["size"] for entry in manifest["files"])
        print(f"Packaged {len(manifest['files'])} local GGUF files, {total} bytes; integrity metadata written")


if __name__ == "__main__":
    main()
