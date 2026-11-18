"""Accounts, opaque cookie sessions, and persistent monthly admission accounting."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from contextvars import ContextVar
from datetime import datetime, timezone

import research_store as store

CURRENT_USER = ContextVar('account', default=None)
COOKIE = 'research_session'
SESSION_SECONDS = 7 * 86400


class AccountError(ValueError):
    pass


def month():
    return datetime.now(timezone.utc).strftime('%Y-%m')


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + digest


def valid_password(password):
    if not 12 <= len(password) <= 128:
        raise AccountError('密码需为 12–128 个字符。')


def initialize():
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
            role TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
            max_runs INTEGER NOT NULL DEFAULT 20, max_calls INTEGER NOT NULL DEFAULT 400,
            max_tokens INTEGER NOT NULL DEFAULT 2000000)''')
        db.execute('CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL)')
        db.execute('''CREATE TABLE IF NOT EXISTS account_usage (
            user_id TEXT NOT NULL, month TEXT NOT NULL, runs INTEGER NOT NULL DEFAULT 0,
            calls INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id,month))''')
        db.execute('CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL)')
        for table in ('runs', 'documents'):
            if 'owner_id' not in {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}:
                db.execute(f'ALTER TABLE {table} ADD COLUMN owner_id TEXT')
            db.execute(f'CREATE INDEX IF NOT EXISTS {table}_owner ON {table}(owner_id)')
        admin = db.execute("SELECT id FROM users WHERE role='admin' ORDER BY created_at LIMIT 1").fetchone()
        if not admin:
            password = secrets.token_urlsafe(18)
            user_id = secrets.token_hex(16)
            # Write recovery information before commit, never print credentials to logs.
            path = store.DB_FILE.parent / 'admin-first-login.txt'
            with path.open('w', encoding='utf-8') as output:
                output.write('深研初始管理员（请登录后修改密码）\n用户名：admin\n密码：' + password + '\n')
            path.chmod(0o600)
            db.execute("INSERT INTO users (id,username,password_hash,role,created_at,max_runs,max_calls,max_tokens) VALUES (?,?,?,'admin',?,1000,20000,100000000)",
                       (user_id, 'admin', password_hash(password), store.now()))
            admin = {'id': user_id}
        for table in ('runs', 'documents'):
            db.execute(f'UPDATE {table} SET owner_id=? WHERE owner_id IS NULL', (admin['id'],))
        db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
        db.execute('DELETE FROM login_attempts WHERE until < ?', (time.time(),))


def user(user_id):
    with store.connect() as db:
        row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    return dict(row) if row else None


def public(account):
    if not account:
        return None
    with store.connect() as db:
        row = db.execute('SELECT runs,calls,tokens FROM account_usage WHERE user_id=? AND month=?', (account['id'], month())).fetchone()
    return {k: account[k] for k in ('id', 'username', 'role', 'enabled', 'max_runs', 'max_calls', 'max_tokens')} | {
        'month': month(), 'usage': dict(row) if row else {'runs': 0, 'calls': 0, 'tokens': 0}}


def all_users():
    with store.connect() as db:
        ids = [r['id'] for r in db.execute('SELECT id FROM users ORDER BY created_at')]
    return [public(user(uid)) for uid in ids]


def create_user(username, password, limits):
    username = username.strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{2,31}', username):
        raise AccountError('用户名需为 3–32 位小写字母、数字、下划线或短横线。')
    valid_password(password)
    validate_limits(limits)
    import sqlite3
    uid = secrets.token_hex(16)
    try:
        with store.connect() as db:
            db.execute("INSERT INTO users (id,username,password_hash,role,created_at,max_runs,max_calls,max_tokens) VALUES (?,?,?,'member',?,?,?,?)",
                       (uid, username, password_hash(password), store.now(), limits['max_runs'], limits['max_calls'], limits['max_tokens']))
    except sqlite3.IntegrityError:
        raise AccountError('用户名已存在。') from None
    return public(user(uid))


def register(username, password, peer):
    key = 'register:' + hashlib.sha256(peer.encode()).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM login_attempts WHERE key=?', (key,)).fetchone()
        if row and row['until'] > time.time() and row['count'] >= 10:
            raise AccountError('注册尝试过多，请一小时后再试。')
        db.execute('INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=CASE WHEN until<? THEN 1 ELSE count+1 END,until=CASE WHEN until<? THEN excluded.until ELSE until END',
                   (key,time.time()+3600,time.time(),time.time()))
    return create_user(username, password, {'max_runs':20, 'max_calls':400, 'max_tokens':2000000})


def validate_limits(limits):
    for key, maximum in (('max_runs',10000), ('max_calls',100000), ('max_tokens',1000000000)):
        if type(limits[key]) is not int or not 0 <= limits[key] <= maximum:
            raise AccountError('额度必须为有效的非负整数，0 表示禁止使用。')


def change_user(uid, limits, enabled):
    validate_limits(limits)
    with store.connect() as db:
        if not db.execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone():
            raise AccountError('账号不存在。')
        db.execute('UPDATE users SET enabled=?,max_runs=?,max_calls=?,max_tokens=? WHERE id=?',
                   (int(enabled), limits['max_runs'], limits['max_calls'], limits['max_tokens'], uid))
        if not enabled:
            db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
    return public(user(uid))


def new_session(uid):
    token = secrets.token_urlsafe(32)
    with store.connect() as db:
        db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), uid, time.time()+SESSION_SECONDS))
    return token


def session_user(token):
    if not token or len(token) > 200:
        return None
    with store.connect() as db:
        row = db.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.hash=? AND s.expires>? AND u.enabled=1',
                         (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
    return dict(row) if row else None


def logout(token):
    with store.connect() as db:
        db.execute('DELETE FROM sessions WHERE hash=?', (hashlib.sha256((token or '').encode()).hexdigest(),))


def login(username, password, peer):
    username = username.strip().lower()
    key = hashlib.sha256((peer + ':' + username).encode()).hexdigest()
    global_key = hashlib.sha256(('peer:' + peer).encode()).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for k, limit in ((key, 8), (global_key, 60)):
            row = db.execute('SELECT * FROM login_attempts WHERE key=?', (k,)).fetchone()
            if row and row['until'] > time.time() and row['count'] >= limit:
                raise AccountError('登录尝试过多，请 15 分钟后重试。')
        row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        for k in (key, global_key):
            db.execute('INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=CASE WHEN until<? THEN 1 ELSE count+1 END,until=CASE WHEN until<? THEN excluded.until ELSE until END',
                       (k,time.time()+900,time.time(),time.time()))
    encoded = row['password_hash'] if row else password_hash('dummy-password-for-timing', '0'*32)
    salt = encoded.split(':')[0]
    correct = hmac.compare_digest(password_hash(password, salt), encoded)
    if not row or not correct or not row['enabled']:
        raise AccountError('用户名或密码错误，或账号已停用。')
    with store.connect() as db:
        db.execute('DELETE FROM login_attempts WHERE key=?', (key,))
    return dict(row)


def change_password(uid, old, new):
    valid_password(new)
    account = user(uid)
    if not hmac.compare_digest(password_hash(old, account['password_hash'].split(':')[0]), account['password_hash']):
        raise AccountError('当前密码不正确。')
    with store.connect() as db:
        db.execute('UPDATE users SET password_hash=? WHERE id=?', (password_hash(new), uid))
        db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
    path = store.DB_FILE.parent / 'admin-first-login.txt'
    if account['role'] == 'admin' and path.exists():
        path.unlink()


def admit(uid, kind):
    """Atomic, persistent admission, independent of deletable research records."""
    period = month()
    if not uid:
        return period
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        account = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not account or not account['enabled']:
            raise AccountError('账号已停用，无法继续调用。')
        db.execute('INSERT OR IGNORE INTO account_usage (user_id,month) VALUES (?,?)', (uid,period))
        usage = db.execute('SELECT * FROM account_usage WHERE user_id=? AND month=?', (uid,period)).fetchone()
        if usage['tokens'] >= account['max_tokens'] or usage['calls'] >= account['max_calls']:
            raise AccountError('本月模型调用或 token 额度已用完，请联系管理员。')
        if kind == 'runs' and usage['runs'] >= account['max_runs']:
            raise AccountError('本月研究次数已用完，请联系管理员。')
        if kind not in {'runs', 'calls'}:
            raise ValueError('Invalid usage kind')
        db.execute(f'UPDATE account_usage SET {kind}={kind}+1 WHERE user_id=? AND month=?', (uid,period))
    return period


def record_tokens(uid, period, tokens):
    if uid and tokens:
        with store.connect() as db:
            db.execute('INSERT INTO account_usage (user_id,month,tokens) VALUES (?,?,?) ON CONFLICT(user_id,month) DO UPDATE SET tokens=tokens+excluded.tokens', (uid,period,max(0,int(tokens))))
