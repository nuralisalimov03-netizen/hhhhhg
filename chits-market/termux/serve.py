#!/usr/bin/env python3
"""Single-instance loopback launcher, usable in Termux without third-party packages."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sys

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT/'server'))
from app import Store, MarketServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Порт должен быть от 1024 до 65535.')
    directory = args.data_dir.expanduser().resolve()
    if not (directory/'market.sqlite3').is_file():
        parser.error('Сначала выполните bash termux/setup.sh.')
    web = PROJECT/'app/src/main/assets/web'
    if not (web/'index.html').is_file():
        parser.error('Не найден веб-интерфейс; сохраните структуру исходного проекта.')
    os.umask(0o077)
    # flock releases automatically on exit/crash; a stale PID never blocks restart
    # and the launcher never kills a process it did not start.
    with (directory/'termux-server.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Сервер с этим каталогом данных уже запущен. Второй экземпляр не создан.', flush=True)
            return
        store = Store(directory)
        if not store.has_admin():
            parser.error('Администратор не создан; выполните bash termux/setup.sh.')
        try:
            server = MarketServer(('127.0.0.1', args.port), store, web_dir=web)
        except OSError as error:
            parser.error(f'Не удалось открыть локальный порт {args.port}: {error.strerror}. Выберите --port с другим номером.')
        lock.seek(0)
        lock.truncate()
        json.dump({'pid': os.getpid(), 'host':'127.0.0.1', 'port':server.server_port}, lock)
        lock.flush()
        print(f'Chits Market запущен на 127.0.0.1:{server.server_port}. Остановка: Ctrl+C.', flush=True)
        print('На этом же телефоне укажите этот HTTP-адрес в настройках сервера маркета. Firebase для локального режима должен быть выключен.', flush=True)

        def stop(_signum, _frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print('\nСервер остановлен; каталог и администратор сохранены.', flush=True)
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
