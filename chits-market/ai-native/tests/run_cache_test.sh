#!/usr/bin/env bash
set -euo pipefail
CHITS_TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHITS_PROJECT_DIR="$(cd -- "$CHITS_TEST_DIR/../.." && pwd)"
CHITS_TOOLS_DIR="${CHITS_TOOLS_DIR:-/workspace/.chits-tools}"
CHITS_TEST_BUILD="$CHITS_TOOLS_DIR/ai-java-cache-test"
mkdir -p "$CHITS_TEST_BUILD/include" "$CHITS_TEST_BUILD/classes"
# NDK's JNI header is explicitly VM-neutral; isolate it from Android libc headers.
cp "$CHITS_TOOLS_DIR/android-ndk-r27d/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include/jni.h" "$CHITS_TEST_BUILD/include/jni.h"
g++ -std=c++17 -shared -fPIC -O2 -I "$CHITS_TEST_BUILD/include" \
  "$CHITS_TEST_DIR/cache_stub.cpp" -o "$CHITS_TEST_BUILD/libchits_ai.so"
java -jar "$CHITS_TOOLS_DIR/ecj.jar" -8 -d "$CHITS_TEST_BUILD/classes" \
  "$CHITS_PROJECT_DIR/app/src/main/java/market/chits/OfflineAi.java" "$CHITS_TEST_DIR/ModelCacheTest.java"
java -Djava.library.path="$CHITS_TEST_BUILD" -cp "$CHITS_TEST_BUILD/classes" market.chits.ModelCacheTest
