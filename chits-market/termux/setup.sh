#!/usr/bin/env bash
# This script never saves an administrator password in source or shell history.
set +x
set -euo pipefail
umask 077
CHITS_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CHITS_TERMUX_HOME="${CHITS_TERMUX_HOME:-$HOME/.local/share/chits-market}"
CHITS_PASSWORD_STDIN=false
if [[ "${1:-}" == "--password-stdin" && $# == 1 ]]; then
  CHITS_PASSWORD_STDIN=true
elif [[ $# != 0 ]]; then
  printf '%s\n' 'Использование: bash termux/setup.sh [--password-stdin]' >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
  if ! command -v pkg >/dev/null 2>&1; then
    printf '%s\n' 'Нужны Python 3.12+ и Git. В Termux: pkg install python git' >&2
    exit 1
  fi
  pkg install -y python git
fi
python3 - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit('Нужен Python 3.12+; обновите пакет Python в Termux.')
PY
mkdir -p -- "$CHITS_TERMUX_HOME"

# Exit status 0 means initialized, 3 means a password still needs to be set.
CHITS_ADMIN_STATE=0
PYTHONPATH="$CHITS_PROJECT_DIR/server" python3 - "$CHITS_TERMUX_HOME" <<'PY' || CHITS_ADMIN_STATE=$?
import sys
from app import Store
raise SystemExit(0 if Store(sys.argv[1]).has_admin() else 3)
PY
if [[ "$CHITS_ADMIN_STATE" == 3 ]]; then
  if "$CHITS_PASSWORD_STDIN"; then
    IFS= read -r CHITS_NEW_PASSWORD || { printf '%s\n' 'Не передан пароль через stdin.' >&2; exit 1; }
  else
    if [[ ! -t 0 ]]; then
      printf '%s\n' 'Для первого запуска нужен терминал для скрытого ввода. Для автоматизации используйте --password-stdin.' >&2
      exit 1
    fi
    IFS= read -r -s -p 'Пароль администратора Leon_Salimov (минимум 8 символов, ввод скрыт): ' CHITS_NEW_PASSWORD
    printf '\n' >&2
  fi
  if [[ ${#CHITS_NEW_PASSWORD} -lt 8 || ${#CHITS_NEW_PASSWORD} -gt 128 ]]; then
    unset CHITS_NEW_PASSWORD
    printf '%s\n' 'Пароль должен содержать от 8 до 128 символов.' >&2
    exit 1
  fi
  printf '%s\n' "$CHITS_NEW_PASSWORD" | python3 "$CHITS_PROJECT_DIR/server/app.py" init --data-dir "$CHITS_TERMUX_HOME" --password-stdin
  unset CHITS_NEW_PASSWORD
elif [[ "$CHITS_ADMIN_STATE" != 0 ]]; then
  printf '%s\n' 'Не удалось проверить данные сервера; существующие файлы сохранены.' >&2
  exit "$CHITS_ADMIN_STATE"
else
  printf '%s\n' 'Администратор уже создан; пароль и каталог сохраняются.'
fi
python3 "$CHITS_PROJECT_DIR/server/app.py" seed --data-dir "$CHITS_TERMUX_HOME" --repo "$(dirname -- "$CHITS_PROJECT_DIR")"
printf '%s\n' 'Подготовка завершена. Запуск: bash termux/start.sh'
printf '%s\n' 'Данные остаются в приватном каталоге Termux; не публикуйте его.'
