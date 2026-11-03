#!/usr/bin/env python3
"""Chits Market's dependency-free APK catalog and authenticated upload API."""
import argparse
import base64
import contextlib
import hashlib
import hmac
import json
import mmap
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import uuid
import zipfile

MAX_APK = 200 * 1024 * 1024
MAX_REQUEST = MAX_APK + 65536
ADMIN_NAME = 'Leon_Salimov'
SESSION_SECONDS = 24 * 3600


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000)
    return 'pbkdf2_sha256$600000$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()


def password_matches(password, encoded):
    try:
        kind, rounds, salt, expected = encoded.split('$')
        if kind != 'pbkdf2_sha256' or int(rounds) != 600000:
            return False
        actual = password_hash(password, base64.b64decode(salt)).split('$')[-1]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# Used only to keep unknown-user login work equivalent to an existing account.
DUMMY_PASSWORD_HASH = password_hash('not-a-valid-password', bytes(16))


def manifest_metadata(raw):
    """Read the root manifest's package/version attributes, including binary AXML."""
    result = {}
    if raw.startswith(b'<'):
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(raw)
            result = {'packageName': root.attrib.get('package', ''),
                      'versionName': root.attrib.get('{http://schemas.android.com/apk/res/android}versionName', '')}
        except ET.ParseError:
            pass
        return result
    if len(raw) < 8 or struct.unpack_from('<H', raw)[0] != 3:
        return result
    strings = []
    offset = struct.unpack_from('<H', raw, 2)[0]
    try:
        while offset + 8 <= len(raw):
            kind, header, size = struct.unpack_from('<HHI', raw, offset)
            if size < header or size < 8 or offset + size > len(raw):
                break
            if kind == 1:
                count, _, flags, start, _ = struct.unpack_from('<IIIII', raw, offset + 8)
                if count > 100000:
                    break
                def length8(p):
                    n = raw[p]
                    return (((n & 127) << 8) | raw[p+1], p+2) if n & 128 else (n, p+1)
                def length16(p):
                    n = struct.unpack_from('<H', raw, p)[0]
                    return (((n & 32767) << 16) | struct.unpack_from('<H', raw, p+2)[0], p+4) if n & 32768 else (n, p+2)
                for index in range(count):
                    p = offset + start + struct.unpack_from('<I', raw, offset + header + index * 4)[0]
                    if flags & 256:
                        _, p = length8(p)
                        length, p = length8(p)
                        strings.append(raw[p:p+length].decode('utf-8', 'replace'))
                    else:
                        length, p = length16(p)
                        strings.append(raw[p:p+length*2].decode('utf-16le', 'replace'))
            elif kind == 0x102 and strings:
                name = struct.unpack_from('<I', raw, offset + 20)[0]
                if name < len(strings) and strings[name] == 'manifest':
                    attr_start, attr_size, attr_count = struct.unpack_from('<HHH', raw, offset + 24)
                    for index in range(attr_count):
                        p = offset + 16 + attr_start + index * attr_size
                        _, key, raw_value, _, _, value_type, value = struct.unpack_from('<IIIHBBI', raw, p)
                        key_name = strings[key] if key < len(strings) else ''
                        text = strings[raw_value] if raw_value < len(strings) else (strings[value] if value_type == 3 and value < len(strings) else str(value))
                        if key_name == 'package':
                            result['packageName'] = text
                        elif key_name == 'versionName':
                            result['versionName'] = text
                    return result
            offset += size
    except (IndexError, struct.error):
        pass
    return result


def inspect_apk(path):
    if path.stat().st_size > MAX_APK:
        raise APIError(413, 'APK больше 200 МБ')
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise APIError(400, 'В APK есть повторяющиеся записи')
            if not {'AndroidManifest.xml', 'classes.dex'}.issubset(names):
                raise APIError(400, 'Файл не является APK: нет манифеста или classes.dex')
            total = 0
            for entry in archive.infolist():
                parts = Path(entry.filename).parts
                if entry.filename.startswith('/') or '..' in parts or entry.flag_bits & 1:
                    raise APIError(400, 'Недопустимые записи APK')
                total += entry.file_size
                if total > 1024 * 1024 * 1024 or entry.file_size > 512 * 1024 * 1024:
                    raise APIError(400, 'Распакованный APK слишком велик')
            if archive.testzip() is not None:
                raise APIError(400, 'APK повреждён')
            manifest = archive.getinfo('AndroidManifest.xml')
            if manifest.file_size > 4 * 1024 * 1024:
                raise APIError(400, 'Манифест APK слишком велик')
            return manifest_metadata(archive.read(manifest))
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as error:
        raise APIError(400, 'Не удалось прочитать APK') from error


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.files = self.directory / 'files'
        self.files.mkdir(exist_ok=True)
        self.database = self.directory / 'market.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS admins (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS apps (id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                    versionName TEXT NOT NULL, packageName TEXT NOT NULL, category TEXT NOT NULL,
                    size INTEGER NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL);
            ''')
        os.chmod(self.database, 0o600)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def has_admin(self):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM admins LIMIT 1').fetchone() is not None

    def init_admin(self, password, username=ADMIN_NAME):
        if len(password) < 8:
            raise APIError(400, 'Пароль администратора должен содержать минимум 8 символов')
        with self.connect() as db:
            if db.execute('SELECT 1 FROM admins LIMIT 1').fetchone():
                raise APIError(409, 'Администратор уже создан; данные не изменены')
            db.execute('INSERT INTO admins VALUES (?, ?)', (username, password_hash(password)))

    def login(self, username, password):
        with self.connect() as db:
            row = db.execute('SELECT password_hash FROM admins WHERE username=?', (username,)).fetchone()
            encoded = row['password_hash'] if row else DUMMY_PASSWORD_HASH
            if not password_matches(password, encoded):
                raise APIError(401, 'Неверный никнейм или пароль')
            token = secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES (?, ?, ?)', (hashlib.sha256(token.encode()).hexdigest(), username, time.time()+SESSION_SECONDS))
            return {'token': token, 'username': username, 'expiresIn': SESSION_SECONDS}

    def authenticate(self, authorization):
        if not authorization.startswith('Bearer ') or len(authorization) > 200:
            raise APIError(401, 'Войдите как администратор')
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with self.connect() as db:
            if db.execute('SELECT username FROM sessions WHERE token_hash=? AND expires>?', (digest, time.time())).fetchone() is None:
                raise APIError(401, 'Сессия истекла. Войдите снова')
        return digest

    @staticmethod
    def app_json(row):
        value = dict(row)
        value.pop('created', None)
        value['downloadUrl'] = '/files/' + value['id'] + '.apk'
        return value

    def apps(self):
        with self.connect() as db:
            return [self.app_json(row) for row in db.execute('SELECT * FROM apps ORDER BY created DESC')]

    def app(self, identity):
        if not re.fullmatch('[0-9a-f]{32}', identity):
            raise APIError(404, 'Приложение не найдено')
        with self.connect() as db:
            row = db.execute('SELECT * FROM apps WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise APIError(404, 'Приложение не найдено')
            return self.app_json(row)

    def add(self, source, fields):
        title = fields.get('title', '').strip()
        if not title or len(title) > 120:
            raise APIError(400, 'Название должно содержать 1–120 символов')
        description = fields.get('description', '').strip()
        category = fields.get('category', 'Другое').strip() or 'Другое'
        if len(description) > 5000 or len(category) > 60:
            raise APIError(400, 'Описание или категория слишком длинные')
        metadata = inspect_apk(source)
        identity = uuid.uuid4().hex
        with source.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        target = self.files / (identity + '.apk')
        try:
            shutil.copyfile(source, target)
            os.chmod(target, 0o600)
            with self.connect() as db:
                db.execute('INSERT INTO apps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (
                    identity, title, description, metadata.get('versionName', '') or fields.get('versionName', '1.0'),
                    metadata.get('packageName', '') or fields.get('packageName', ''), category,
                    source.stat().st_size, digest, time.time()))
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return self.app(identity)

    def delete(self, identity):
        self.app(identity)
        with self.connect() as db:
            db.execute('DELETE FROM apps WHERE id=?', (identity,))
        (self.files / (identity + '.apk')).unlink(missing_ok=True)


def read_multipart(handler, directory):
    content_type = handler.headers.get('Content-Type', '')
    match = re.fullmatch(r'multipart/form-data;\s*boundary=(?:"([^"]+)"|([^;\s]+))', content_type)
    if not match:
        raise APIError(400, 'Ожидается multipart/form-data')
    boundary_text = match.group(1) or match.group(2)
    if not re.fullmatch(r"[0-9A-Za-z'()+_,./:=? -]{1,70}", boundary_text):
        raise APIError(400, 'Некорректная граница multipart')
    boundary = b'--' + boundary_text.encode('ascii')
    length = handler.body_length(MAX_REQUEST)
    fields = {}
    with tempfile.TemporaryDirectory(prefix='upload-', dir=directory) as folder:
        raw_path, apk_path = Path(folder)/'body', Path(folder)/'file.apk'
        with raw_path.open('wb') as stream:
            remaining = length
            while remaining:
                data = handler.rfile.read(min(65536, remaining))
                if not data:
                    raise APIError(400, 'Передача файла прервана')
                stream.write(data)
                remaining -= len(data)
        if length < len(boundary) + 4:
            raise APIError(400, 'Пустая загрузка')
        with raw_path.open('rb') as raw, mmap.mmap(raw.fileno(), 0, access=mmap.ACCESS_READ) as body:
            if body[:len(boundary)+2] != boundary+b'\r\n':
                raise APIError(400, 'Некорректный multipart')
            offset = len(boundary)+2
            seen = set()
            for _ in range(16):
                header_end = body.find(b'\r\n\r\n', offset, min(offset+8192, length))
                if header_end == -1:
                    raise APIError(400, 'Некорректные заголовки загрузки')
                headers = body[offset:header_end].decode('latin1')
                disposition = re.search(r'(?im)^Content-Disposition:\s*form-data;([^\r\n]+)', headers)
                name_match = re.search(r'(?:^|;)\s*name="([A-Za-z][A-Za-z0-9]*)"', disposition.group(1) if disposition else '')
                if not name_match or name_match.group(1) in seen:
                    raise APIError(400, 'Некорректное или повторяющееся поле загрузки')
                name = name_match.group(1)
                seen.add(name)
                data_start = header_end+4
                data_end = body.find(b'\r\n'+boundary, data_start)
                if data_end == -1:
                    raise APIError(400, 'Загрузка не завершена')
                if name == 'file':
                    if data_end-data_start > MAX_APK:
                        raise APIError(413, 'APK больше 200 МБ')
                    with apk_path.open('wb') as target:
                        raw.seek(data_start)
                        remaining = data_end-data_start
                        while remaining:
                            chunk = raw.read(min(65536, remaining))
                            target.write(chunk)
                            remaining -= len(chunk)
                elif name in {'title', 'description', 'category', 'versionName', 'packageName'}:
                    if data_end-data_start > 20000:
                        raise APIError(400, 'Текстовое поле слишком большое')
                    try:
                        fields[name] = body[data_start:data_end].decode('utf-8')
                    except UnicodeDecodeError as error:
                        raise APIError(400, 'Текст должен быть в UTF-8') from error
                else:
                    raise APIError(400, 'Неизвестное поле загрузки')
                offset = data_end+2+len(boundary)
                if body[offset:offset+2] == b'--':
                    if body[offset+2:] not in (b'', b'\r\n'):
                        raise APIError(400, 'Лишние данные после multipart')
                    break
                if body[offset:offset+2] != b'\r\n':
                    raise APIError(400, 'Некорректная граница загрузки')
                offset += 2
            else:
                raise APIError(400, 'Слишком много полей')
        if not apk_path.is_file():
            raise APIError(400, 'Прикрепите APK в поле file')
        yield apk_path, fields


class MarketServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, store, cors_origin='*', web_dir=None):
        self.store, self.cors_origin = store, cors_origin
        self.web_dir = Path(web_dir).resolve() if web_dir else None
        self.login_failures = {}
        self.rate_lock = threading.Lock()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'ChitsMarket/1.0'
    # One request per connection keeps rejected/oversized bodies from being reused.
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def log_message(self, fmt, *args):
        # Never log request bodies, passwords, headers, or tokens.
        sys.stderr.write('%s %s\n' % (self.client_address[0], fmt % args))

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', self.server.cors_origin)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Expose-Headers', 'Content-Length, X-Content-SHA256')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def json_response(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def body_length(self, maximum):
        if self.headers.get('Transfer-Encoding'):
            raise APIError(400, 'Используйте Content-Length')
        if len(self.headers.get_all('Content-Length', [])) > 1:
            raise APIError(400, 'Повторяющийся Content-Length')
        try:
            length = int(self.headers.get('Content-Length', '-1'))
        except ValueError as error:
            raise APIError(400, 'Некорректный Content-Length') from error
        if length < 0:
            raise APIError(411, 'Требуется Content-Length')
        if length > maximum:
            raise APIError(413, 'Запрос слишком большой')
        return length

    def json_body(self):
        length = self.body_length(16384)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise APIError(400, 'Передача прервана')
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise APIError(400, 'Некорректный JSON') from error
        if not isinstance(value, dict):
            raise APIError(400, 'Ожидается JSON-объект')
        return value

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def run_request(self):
        try:
            self.dispatch()
        except APIError as error:
            self.json_response(error.status, {'error': error.message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.json_response(500, {'error': 'Внутренняя ошибка сервера'})

    do_GET = do_POST = do_DELETE = run_request

    def dispatch(self):
        path = urlsplit(self.path).path
        store = self.server.store
        if self.command == 'GET' and path == '/health':
            return self.json_response(200, {'status': 'ok', 'adminConfigured': store.has_admin(), 'service': 'Chits Market'})
        if self.command == 'GET' and path == '/api/apps':
            return self.json_response(200, {'apps': store.apps()})
        if self.command == 'POST' and path == '/api/auth/login':
            data = self.json_body()
            username, password = data.get('username'), data.get('password')
            if not isinstance(username, str) or not isinstance(password, str) or len(username)>120 or len(password)>1024:
                raise APIError(400, 'Укажите никнейм и пароль')
            key = (self.client_address[0], username)
            now = time.monotonic()
            with self.server.rate_lock:
                if len(self.server.login_failures)>10000:
                    self.server.login_failures = {k:v for k,v in self.server.login_failures.items() if v[-1]>now-900}
                attempts = [x for x in self.server.login_failures.get(key, []) if x > now-900]
                if len(attempts) >= 5:
                    raise APIError(429, 'Слишком много попыток. Повторите через 15 минут')
            try:
                result = store.login(username, password)
            except APIError:
                with self.server.rate_lock:
                    self.server.login_failures.setdefault(key, []).append(now)
                raise
            with self.server.rate_lock:
                self.server.login_failures.pop(key, None)
            return self.json_response(200, result)
        if self.command == 'POST' and path == '/api/auth/logout':
            digest = store.authenticate(self.headers.get('Authorization', ''))
            with store.connect() as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (digest,))
            return self.json_response(200, {'ok': True})
        if self.command == 'POST' and path == '/api/apps':
            store.authenticate(self.headers.get('Authorization', ''))
            with contextlib.contextmanager(read_multipart)(self, store.directory) as (file, fields):
                result = store.add(file, fields)
            return self.json_response(201, result)
        if self.command == 'DELETE' and path.startswith('/api/apps/'):
            store.authenticate(self.headers.get('Authorization', ''))
            store.delete(path[len('/api/apps/'):])
            return self.json_response(200, {'ok': True})
        match = re.fullmatch(r'/files/([0-9a-f]{32})\.apk', path)
        if self.command == 'GET' and match:
            item = store.app(match.group(1))
            file = store.files / (item['id'] + '.apk')
            if not file.is_file():
                raise APIError(404, 'Файл APK недоступен')
            self.send_response(200)
            self.send_header('Content-Type', 'application/vnd.android.package-archive')
            self.send_header('Content-Length', str(item['size']))
            self.send_header('Content-Disposition', 'attachment; filename="' + item['id'] + '.apk"')
            self.send_header('X-Content-SHA256', item['sha256'])
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
            self.end_headers()
            with file.open('rb') as stream:
                shutil.copyfileobj(stream, self.wfile)
            return
        if self.command == 'GET' and self.server.web_dir is not None:
            relative = unquote(path).lstrip('/') or 'index.html'
            if '\\' in relative or '\x00' in relative:
                raise APIError(404, 'Файл не найден')
            base = self.server.web_dir
            candidate = (base / relative).resolve()
            # Resolve symlinks before the containment check; never expose the
            # neighboring source tree, SQLite database, uploads or password files.
            if not candidate.is_relative_to(base) or not candidate.is_file():
                raise APIError(404, 'Файл не найден')
            allowed = {'.html', '.css', '.js', '.json', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.ico', '.woff', '.woff2'}
            if candidate.suffix.lower() not in allowed:
                raise APIError(404, 'Файл не найден')
            content_type = mimetypes.guess_type(candidate.name)[0] or 'application/octet-stream'
            if candidate.suffix.lower() == '.js':
                content_type = 'application/javascript'
            if candidate.suffix.lower() in {'.html', '.css', '.js', '.json'}:
                content_type += '; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(candidate.stat().st_size))
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Content-Security-Policy', "frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
            self.end_headers()
            with candidate.open('rb') as stream:
                shutil.copyfileobj(stream, self.wfile)
            return
        raise APIError(404, 'Адрес не найден')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'serve', 'seed'])
    parser.add_argument('--data-dir', default=os.environ.get('CHITS_DATA_DIR', str(Path.home()/'.local/share/chits-market')))
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--username', default=ADMIN_NAME)
    parser.add_argument('--password-stdin', action='store_true')
    parser.add_argument('--password-file', type=Path)
    parser.add_argument('--repo', type=Path, default=Path('/workspace/hhhhhg'))
    parser.add_argument('--cors-origin', default='*')
    default_web = Path(os.environ['CHITS_WEB_DIR']) if os.environ.get('CHITS_WEB_DIR') else Path(__file__).resolve().parent.parent/'app/src/main/assets/web'
    parser.add_argument('--web-dir', type=Path, default=default_web)
    args = parser.parse_args()
    store = Store(args.data_dir)
    if args.command == 'init' or (args.command == 'serve' and not store.has_admin()):
        password = os.environ.get('CHITS_ADMIN_PASSWORD')
        if args.password_file:
            password = args.password_file.read_text().rstrip('\r\n')
        elif args.password_stdin:
            password = sys.stdin.readline().rstrip('\r\n')
        if password is not None:
            store.init_admin(password, args.username)
            password = None
            print('Администратор создан; сохранён только хеш пароля.')
        elif args.command == 'init':
            parser.error('Передайте пароль через --password-stdin, --password-file или CHITS_ADMIN_PASSWORD')
    if args.command == 'seed':
        for name, title, description in [
            ('Orbita-v6.apk', 'Орбита', '45 офлайн-игр: головоломки, настольные игры и 3D-аркады.'),
            ('Breach-v2.apk', 'Прорыв', 'Офлайн-шутер с ботами 3 × 3.')]:
            source = args.repo/name
            if not source.is_file():
                bundled = {'Orbita-v6.apk':'orbita.apk', 'Breach-v2.apk':'breach.apk'}
                source = Path(__file__).resolve().parent.parent/'app/src/main/assets/apks'/bundled[name]
            with source.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if any(item['sha256']==digest for item in store.apps()):
                print(title + ': уже в каталоге')
                continue
            item = store.add(source, {'title':title, 'description':description, 'category':'Игры'})
            print(title + ': добавлено ' + item['id'])
    elif args.command == 'serve':
        if not store.has_admin():
            parser.error('Сначала создайте администратора командой init')
        if not (args.web_dir/'index.html').is_file():
            parser.error('Не найден интерфейс: укажите --web-dir с index.html')
        server = MarketServer((args.host, args.port), store, args.cors_origin, args.web_dir)
        print(f'Chits Market API listening on {args.host}:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
