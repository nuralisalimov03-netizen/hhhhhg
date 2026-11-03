#!/usr/bin/env bash
set -euo pipefail
CHITS_TOOLS_DIR="${CHITS_TOOLS_DIR:-/workspace/.chits-tools}"
mkdir -p "$CHITS_TOOLS_DIR"
fetch() {
  local name="$1" url="$2" sha="$3"
  if [[ ! -f "$CHITS_TOOLS_DIR/$name" ]]; then
    curl --fail --location --retry 2 "$url" -o "$CHITS_TOOLS_DIR/$name"
  fi
  printf '%s  %s\n' "$sha" "$CHITS_TOOLS_DIR/$name" | sha256sum -c -
}
fetch build-tools.zip https://dl.google.com/android/repository/build-tools_r35_linux.zip bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88
fetch platform.zip https://dl.google.com/android/repository/platform-35_r02.zip 0988cacad01b38a18a47bac14a0695f246bc76c1b06c0eeb8eb0dc825ab0c8e0
fetch ecj.jar https://repo.maven.apache.org/maven2/org/eclipse/jdt/ecj/3.41.0/ecj-3.41.0.jar 47bda183cd94a8bdb704f5b35690224f75b7d2fb563c8b8f9bbcfab987c3029b
[[ -x "$CHITS_TOOLS_DIR/build-tools/android-15/aapt" ]] || unzip -q -o "$CHITS_TOOLS_DIR/build-tools.zip" -d "$CHITS_TOOLS_DIR/build-tools"
[[ -f "$CHITS_TOOLS_DIR/platform/android-35/android.jar" ]] || unzip -q -o "$CHITS_TOOLS_DIR/platform.zip" -d "$CHITS_TOOLS_DIR/platform"
java -version
