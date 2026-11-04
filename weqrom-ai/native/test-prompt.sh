#!/usr/bin/env bash
set -euo pipefail
WEQROM_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WEQROM_TOOLS_DIR="${WEQROM_TOOLS_DIR:-/workspace/.chits-tools}"
WEQROM_TEST_DIR="$(mktemp -d /tmp/weqrom-prompt-test-XXXXXX)"
trap 'rm -rf -- "$WEQROM_TEST_DIR"' EXIT
java -jar "$WEQROM_TOOLS_DIR/ecj.jar" -1.8 -proc:none -warn:none \
  -classpath "$WEQROM_TOOLS_DIR/json-20250107.jar" -d "$WEQROM_TEST_DIR" \
  "$WEQROM_PROJECT_DIR/app/src/main/java/app/weqrom/ai/PromptFormatter.java" "$WEQROM_PROJECT_DIR/native/PromptFormatterTest.java"
java -cp "$WEQROM_TEST_DIR:$WEQROM_TOOLS_DIR/json-20250107.jar" PromptFormatterTest
