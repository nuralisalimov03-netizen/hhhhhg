#!/usr/bin/env bash
# Reproducible Android ARM64 llama.cpp build; never downloads a language model.
set -euo pipefail
CHITS_AI_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHITS_PROJECT_DIR="$(dirname -- "$CHITS_AI_DIR")"
CHITS_TOOLS_DIR="${CHITS_TOOLS_DIR:-/workspace/.chits-tools}"
CHITS_LLAMA_COMMIT=a7a98e0fffed794396b3fbad4dcdbbc184963645
CHITS_NDK_SHA1=22105e410cf29afcf163760cc95522b9fb981121
mkdir -p "$CHITS_TOOLS_DIR"
if [[ ! -x "$CHITS_TOOLS_DIR/python-tools/cmake/data/bin/cmake" || ! -x "$CHITS_TOOLS_DIR/python-tools/bin/ninja" ]]; then
    python3 -m pip install --disable-pip-version-check --index-url https://pypi.org/simple \
      --target "$CHITS_TOOLS_DIR/python-tools" cmake==3.31.10 ninja==1.13.0
fi
if [[ ! -x "$CHITS_TOOLS_DIR/android-ndk-r27d/toolchains/llvm/prebuilt/linux-x86_64/bin/clang" ]]; then
    if [[ ! -f "$CHITS_TOOLS_DIR/android-ndk-r27d-linux.zip" ]]; then
        curl --fail --location --retry 2 https://dl.google.com/android/repository/android-ndk-r27d-linux.zip \
          -o "$CHITS_TOOLS_DIR/android-ndk-r27d-linux.zip"
    fi
    printf '%s  %s\n' "$CHITS_NDK_SHA1" "$CHITS_TOOLS_DIR/android-ndk-r27d-linux.zip" | sha1sum -c -
    unzip -q -o "$CHITS_TOOLS_DIR/android-ndk-r27d-linux.zip" -d "$CHITS_TOOLS_DIR"
fi
if [[ ! -d "$CHITS_TOOLS_DIR/llama.cpp/.git" ]]; then
    git clone --filter=blob:none --depth 1 --branch b6500 https://github.com/ggml-org/llama.cpp.git "$CHITS_TOOLS_DIR/llama.cpp"
fi
if [[ "$(git -C "$CHITS_TOOLS_DIR/llama.cpp" rev-parse HEAD)" != "$CHITS_LLAMA_COMMIT" ]]; then
    printf '%s\n' 'Unexpected llama.cpp revision; refusing to build. Use a clean b6500 checkout.' >&2
    exit 1
fi
if [[ -n "$(git -C "$CHITS_TOOLS_DIR/llama.cpp" status --porcelain --untracked-files=no)" ]]; then
    printf '%s\n' 'Tracked llama.cpp source changes found; refusing to build modified dependency.' >&2
    exit 1
fi
CHITS_CMAKE="$CHITS_TOOLS_DIR/python-tools/cmake/data/bin/cmake"
"$CHITS_CMAKE" -S "$CHITS_AI_DIR" -B "$CHITS_TOOLS_DIR/ai-build-arm64" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$CHITS_TOOLS_DIR/python-tools/bin/ninja" \
  -DCMAKE_TOOLCHAIN_FILE="$CHITS_TOOLS_DIR/android-ndk-r27d/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26 -DANDROID_STL=c++_static \
  -DLLAMA_SOURCE_DIR="$CHITS_TOOLS_DIR/llama.cpp" -DGGML_CPU_ARM_ARCH=armv8-a -DCMAKE_BUILD_TYPE=Release
"$CHITS_CMAKE" --build "$CHITS_TOOLS_DIR/ai-build-arm64" --target chits_ai -j "${CHITS_BUILD_JOBS:-6}"
mkdir -p "$CHITS_PROJECT_DIR/app/src/main/jniLibs/arm64-v8a"
cp "$CHITS_TOOLS_DIR/ai-build-arm64/libchits_ai.so" "$CHITS_PROJECT_DIR/app/src/main/jniLibs/arm64-v8a/libchits_ai.so"
"$CHITS_TOOLS_DIR/android-ndk-r27d/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip" --strip-unneeded \
  "$CHITS_PROJECT_DIR/app/src/main/jniLibs/arm64-v8a/libchits_ai.so"
sha256sum "$CHITS_PROJECT_DIR/app/src/main/jniLibs/arm64-v8a/libchits_ai.so"
