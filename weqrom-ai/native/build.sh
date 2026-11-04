#!/usr/bin/env bash
# Build only from the checked, already installed dependency sources.
set -euo pipefail
WEQROM_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WEQROM_TOOLS_DIR="${WEQROM_TOOLS_DIR:-/workspace/.chits-tools}"
WEQROM_LLAMA_DIR="$WEQROM_TOOLS_DIR/llama.cpp"
test "$(git -C "$WEQROM_LLAMA_DIR" rev-parse HEAD)" = a7a98e0fffed794396b3fbad4dcdbbc184963645
test -z "$(git -C "$WEQROM_LLAMA_DIR" status --porcelain --untracked-files=no)"
WEQROM_CMAKE="$WEQROM_TOOLS_DIR/python-tools/cmake/data/bin/cmake"
WEQROM_BUILD_DIR="$WEQROM_TOOLS_DIR/weqrom-ai-arm64"
"$WEQROM_CMAKE" -S "$WEQROM_PROJECT_DIR/native" -B "$WEQROM_BUILD_DIR" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$WEQROM_TOOLS_DIR/python-tools/bin/ninja" \
  -DCMAKE_TOOLCHAIN_FILE="$WEQROM_TOOLS_DIR/android-ndk-r27d/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26 -DANDROID_STL=c++_static \
  -DLLAMA_SOURCE_DIR="$WEQROM_LLAMA_DIR" -DGGML_CPU_ARM_ARCH=armv8-a -DCMAKE_BUILD_TYPE=Release
"$WEQROM_CMAKE" --build "$WEQROM_BUILD_DIR" --target weqrom_ai -j "${WEQROM_BUILD_JOBS:-4}"
mkdir -p "$WEQROM_PROJECT_DIR/app/src/main/jniLibs/arm64-v8a"
cp "$WEQROM_BUILD_DIR/libweqrom_ai.so" "$WEQROM_PROJECT_DIR/app/src/main/jniLibs/arm64-v8a/libweqrom_ai.so"
"$WEQROM_TOOLS_DIR/android-ndk-r27d/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip" --strip-unneeded \
  "$WEQROM_PROJECT_DIR/app/src/main/jniLibs/arm64-v8a/libweqrom_ai.so"
