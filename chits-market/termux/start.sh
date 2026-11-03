#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077
CHITS_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CHITS_TERMUX_HOME="${CHITS_TERMUX_HOME:-$HOME/.local/share/chits-market}"
CHITS_TERMUX_PORT="${CHITS_TERMUX_PORT:-8765}"
if [[ $# == 2 && "$1" == '--port' ]]; then
  CHITS_TERMUX_PORT="$2"
elif [[ $# != 0 ]]; then
  printf '%s\n' 'Использование: bash termux/start.sh [--port 8765]' >&2
  printf '%s\n' 'Сервер доступен только на этом телефоне. Для других устройств нужен HTTPS.' >&2
  exit 2
fi
exec python3 "$CHITS_PROJECT_DIR/termux/serve.py" --data-dir "$CHITS_TERMUX_HOME" --port "$CHITS_TERMUX_PORT"
