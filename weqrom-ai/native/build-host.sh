#!/usr/bin/env bash
set -euo pipefail
WEQROM_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WEQROM_TOOLS_DIR="${WEQROM_TOOLS_DIR:-/workspace/.chits-tools}"
WEQROM_LLAMA_DIR="$WEQROM_TOOLS_DIR/llama.cpp"
test "$(git -C "$WEQROM_LLAMA_DIR" rev-parse HEAD)" = a7a98e0fffed794396b3fbad4dcdbbc184963645
test -z "$(git -C "$WEQROM_LLAMA_DIR" status --porcelain --untracked-files=no)"
"$WEQROM_TOOLS_DIR/python-tools/cmake/data/bin/cmake" -S "$WEQROM_PROJECT_DIR/native" -B "$WEQROM_TOOLS_DIR/weqrom-ai-host" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$WEQROM_TOOLS_DIR/python-tools/bin/ninja" -DLLAMA_SOURCE_DIR="$WEQROM_LLAMA_DIR" -DCMAKE_BUILD_TYPE=Release
"$WEQROM_TOOLS_DIR/python-tools/cmake/data/bin/cmake" --build "$WEQROM_TOOLS_DIR/weqrom-ai-host" --target weqrom-ai-run -j "${WEQROM_BUILD_JOBS:-4}"
