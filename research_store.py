from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent / ".data" / "research.sqlite3"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_FILE, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize() -> None:
    with connect() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, question TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, report TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', brief TEXT NOT NULL DEFAULT '', sources TEXT NOT NULL DEFAULT '[]', usage TEXT NOT NULL DEFAULT '{}')")
        connection.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, time TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL)")
        connection.execute("CREATE INDEX IF NOT EXISTS events_run ON events(run_id, seq)")
        connection.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, run_id TEXT, name TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL, size INTEGER NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS versions (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, report TEXT NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS translations (run_id TEXT NOT NULL, source_hash TEXT NOT NULL, language TEXT NOT NULL, report TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id, source_hash, language))")
        connection.execute("CREATE TABLE IF NOT EXISTS workspace_drafts (owner_id TEXT NOT NULL, scope TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(owner_id, scope))")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
        for name, definition in {
            "depth": "TEXT NOT NULL DEFAULT 'standard'",
            "budget": "TEXT NOT NULL DEFAULT '{}'",
            "plan": "TEXT NOT NULL DEFAULT '{}'",
            "notes": "TEXT NOT NULL DEFAULT '[]'",
            "stage": "TEXT NOT NULL DEFAULT ''",
            "elapsed_seconds": "REAL NOT NULL DEFAULT 0",
            "phase_started_at": "TEXT",
            "title": "TEXT NOT NULL DEFAULT ''",
            "pinned": "INTEGER NOT NULL DEFAULT 0",
            "tags": "TEXT NOT NULL DEFAULT '[]'",
            "chats": "TEXT NOT NULL DEFAULT '[]'",
            "checkpointed": "INTEGER NOT NULL DEFAULT 0",
            "operation": "TEXT NOT NULL DEFAULT 'research'",
            "owner_id": "TEXT",
            "report_language": "TEXT NOT NULL DEFAULT 'unknown'",
        }.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")
        if 'owner_id' not in {row['name'] for row in connection.execute('PRAGMA table_info(documents)')}:
            connection.execute('ALTER TABLE documents ADD COLUMN owner_id TEXT')
        for row in connection.execute("SELECT id, usage, elapsed_seconds, phase_started_at FROM runs WHERE status IN ('running','planning')").fetchall():
            elapsed = elapsed_time(dict(row))
            usage = json.loads(row['usage'])
            if usage.get('in_flight'):
                usage.update(in_flight=0, usage_incomplete=True)
            connection.execute("UPDATE runs SET status='interrupted', stage='interrupted', error='服务重启，任务已中断。已收集资料保留，有检查点的研究可继续。', elapsed_seconds=?, phase_started_at=NULL, usage=?, updated_at=? WHERE id=?", (elapsed, json.dumps(usage), now(), row["id"]))


def create(question: str, *, status: str = "running", depth: str = "standard", budget: dict | None = None, report_language: str = 'zh') -> dict:
    from user_accounts import CURRENT_USER
    account = CURRENT_USER.get()
    run_id, stamp = str(uuid.uuid4()), now()
    with connect() as connection:
        connection.execute("INSERT INTO runs (id,question,status,created_at,updated_at,depth,budget,stage,phase_started_at,owner_id) VALUES (?,?,?,?,?,?,?,?,?,?)", (run_id, question, status, stamp, stamp, depth, json.dumps(budget or {}), status, stamp if status in {"running", "planning"} else None, account['id'] if account else None))
        connection.execute('UPDATE runs SET report_language=? WHERE id=?', (report_language, run_id))
    return get(run_id)


def get(run_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['pinned'] = bool(result['pinned'])
        for name in ("sources", "usage", "budget", "plan", "notes", "tags", "chats"):
            result[name] = json.loads(result[name])
        result["elapsed_seconds"] = elapsed_time(result)
        result["events"] = [dict(event) for event in connection.execute("SELECT seq,time,kind,message FROM events WHERE run_id=? ORDER BY seq", (run_id,))]
        result["documents"] = [dict(item) for item in connection.execute("SELECT id,name,size,created_at FROM documents WHERE run_id=?", (run_id,))]
        result["versions"] = [dict(item) for item in connection.execute("SELECT id,label,created_at FROM versions WHERE run_id=? ORDER BY id DESC", (run_id,))]
        result['translations'] = [dict(item) for item in connection.execute('SELECT language,report,created_at FROM translations WHERE run_id=? AND source_hash=?', (run_id, report_hash(result['report'])))]
    return result


def list_runs(query: str = "") -> list[dict]:
    from user_accounts import CURRENT_USER
    account = CURRENT_USER.get()
    with connect() as connection:
        rows = connection.execute("SELECT id,question,title,tags,pinned,status,created_at,updated_at FROM runs" + (" WHERE owner_id=?" if account else '') + " ORDER BY pinned DESC, rowid DESC", (account['id'],) if account else ()).fetchall()
    result = [{**dict(row), "tags": json.loads(row["tags"]), "pinned": bool(row['pinned'])} for row in rows]
    return [r for r in result if query.casefold() in (r['question'] + ' ' + r['title'] + ' ' + ' '.join(r['tags'])).casefold()]


def update(run_id: str, **values) -> None:
    allowed = {"status", "report", "error", "brief", "sources", "usage", "depth", "budget", "plan", "notes", "stage", "elapsed_seconds", "phase_started_at", "title", "tags", "chats", "checkpointed", "operation", "pinned"}
    if not values or set(values) - allowed:
        raise ValueError("Invalid run update")
    values = {key: json.dumps(value, ensure_ascii=False) if key in {"sources", "usage", "budget", "plan", "notes", "tags", "chats"} else value for key, value in values.items()}
    values["updated_at"] = now()
    with connect() as connection:
        connection.execute(f"UPDATE runs SET {', '.join(key + '=?' for key in values)} WHERE id=?", (*values.values(), run_id))


def event(run_id: str, kind: str, message: str) -> None:
    with connect() as connection:
        connection.execute("INSERT INTO events (run_id,time,kind,message) VALUES (?,?,?,?)", (run_id, now(), kind, message[:3000]))


def elapsed_time(run: dict) -> float:
    elapsed = float(run.get("elapsed_seconds") or 0)
    if run.get("phase_started_at"):
        elapsed += max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(run["phase_started_at"])).total_seconds())
    return elapsed


def finish_phase(run_id: str, status: str, **values) -> None:
    run = get(run_id)
    update(run_id, status=status, stage=status, elapsed_seconds=run["elapsed_seconds"], phase_started_at=None, **values)


def begin_phase(run_id: str, status: str, **values) -> None:
    update(run_id, status=status, stage=status, phase_started_at=now(), **values)


def add_document(name: str, text: str, size: int) -> dict:
    from user_accounts import CURRENT_USER
    account = CURRENT_USER.get()
    item = dict(id=str(uuid.uuid4()), name=name, text=text, size=size, created_at=now())
    with connect() as db:
        db.execute("INSERT INTO documents (id,name,text,size,created_at,owner_id) VALUES (:id,:name,:text,:size,:created_at,:owner_id)", {**item, 'owner_id': account['id'] if account else None})
    return {k: v for k, v in item.items() if k != 'text'}


def attach_documents(run_id: str, ids: list[str]):
    with connect() as db:
        items = []
        for doc_id in ids:
            row = db.execute("SELECT * FROM documents WHERE id=? AND run_id IS NULL", (doc_id,)).fetchone()
            if row is None:
                raise ValueError("上传资料不存在或已属于其他研究，请重新上传。")
            items.append(dict(row))
        for item in items:
            db.execute("UPDATE documents SET run_id=? WHERE id=?", (run_id, item['id']))
    return items


def document(doc_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return dict(row) if row else None


def document_context(run_id: str) -> str:
    with connect() as db:
        rows = db.execute("SELECT id,name,text FROM documents WHERE run_id=?", (run_id,)).fetchall()
    return '\n\n'.join(f"文档：{r['name']}\n引用链接：/api/documents/{r['id']}\n内容：\n{r['text']}" for r in rows)


def save_report(run_id: str, report: str, label: str):
    with connect() as db:
        previous = db.execute("SELECT report FROM runs WHERE id=?", (run_id,)).fetchone()['report']
        if previous and not db.execute("SELECT 1 FROM versions WHERE run_id=? AND report=?", (run_id, previous)).fetchone():
            db.execute("INSERT INTO versions (run_id,report,label,created_at) VALUES (?,?,?,?)", (run_id, previous, '原报告', now()))
        db.execute("INSERT INTO versions (run_id,report,label,created_at) VALUES (?,?,?,?)", (run_id, report, label, now()))
        db.execute("UPDATE runs SET report=?,updated_at=? WHERE id=?", (report, now(), run_id))


def version(run_id: str, version_id: int):
    with connect() as db:
        row = db.execute("SELECT * FROM versions WHERE run_id=? AND id=?", (run_id, version_id)).fetchone()
    return dict(row) if row else None


def delete(run_id: str):
    with connect() as db:
        db.execute("DELETE FROM workspace_drafts WHERE scope=?", ('plan:' + run_id,))
        for table in ('events', 'documents', 'versions', 'translations'):
            db.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
        db.execute("DELETE FROM runs WHERE id=?", (run_id,))


class DraftConflict(ValueError):
    pass


def read_draft(owner_id: str, scope: str) -> dict:
    with connect() as db:
        row = db.execute('SELECT revision,data,updated_at FROM workspace_drafts WHERE owner_id=? AND scope=?', (owner_id, scope)).fetchone()
    return {'revision': row['revision'], 'data': json.loads(row['data']) if row['data'] else None, 'updated_at': row['updated_at']} if row else {'revision': 0, 'data': None, 'updated_at': None}


def write_draft(owner_id: str, scope: str, data: dict | None, revision: int | None = None) -> dict:
    # Keep tombstones so a stale browser cannot recreate a submitted/cleared draft.
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT revision FROM workspace_drafts WHERE owner_id=? AND scope=?', (owner_id, scope)).fetchone()
        current = row['revision'] if row else 0
        if revision is not None and revision != current:
            raise DraftConflict('另一页面已更新这份草稿，请重新载入后再编辑。')
        stamp = now()
        db.execute('INSERT INTO workspace_drafts VALUES (?,?,?,?,?) ON CONFLICT(owner_id,scope) DO UPDATE SET revision=excluded.revision,data=excluded.data,updated_at=excluded.updated_at',
                   (owner_id, scope, current + 1, json.dumps(data, ensure_ascii=False) if data is not None else None, stamp))
    return {'revision': current + 1, 'data': data, 'updated_at': stamp}


def report_hash(report):
    return hashlib.sha256(report.encode()).hexdigest()


def save_translation(run_id, source, language, report):
    with connect() as db:
        current = db.execute('SELECT report FROM runs WHERE id=?', (run_id,)).fetchone()
        if not current or report_hash(current['report']) != report_hash(source):
            raise ValueError('原报告已变化，请重新翻译。')
        db.execute('INSERT INTO translations VALUES (?,?,?,?,?) ON CONFLICT(run_id,source_hash,language) DO UPDATE SET report=excluded.report,created_at=excluded.created_at',
                   (run_id,report_hash(source),language,report,now()))


def partial_report(run: dict) -> str:
    if not run['notes'] and not run['sources']:
        return ''
    text = '# 阶段资料汇总\n\n> 研究尚未完成。以下为已保存笔记与检索摘录，未经最终综合核验。\n\n'
    text += '## 已完成笔记\n\n' + ('\n\n'.join(run['notes']) or '暂无完成的笔记。')
    text += '\n\n## 已收集来源\n\n'
    for s in run['sources']:
        text += f"### {s['title']}\n\n来源：{s['url']}\n\n{s.get('excerpt') or '该来源未保存摘录。'}\n\n"
    return text
