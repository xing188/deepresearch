from __future__ import annotations

import asyncio
import json
import re
import socket
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from typing import Literal

import bleach
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import research_store as store
from local_config import public_settings, save_settings, settings
from research_budget import BudgetSettings
from research_plan import ResearchPlan, plan_brief
import mobile_access
import user_accounts as accounts

ROOT = Path(__file__).resolve().parent
JOBS: dict[str, asyncio.Task] = {}
LAN_ACCESS: dict | None = None
TAILSCALE_HOST: str | None = None
DEPTHS = {"quick", "standard", "deep"}
MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.initialize()
    accounts.initialize()
    yield
    tasks = list(JOBS.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    JOBS.clear()


app = FastAPI(title="Open Deep Research Local", lifespan=lifespan)


def tailscale_hostname(value: str) -> str:
    value = value.lower()
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if len(value) > 253 or not re.fullmatch(rf'{label}(?:\.{label})+\.ts\.net', value):
        raise ValueError('Use the exact Tailscale DNS name, such as deepresearch.example.ts.net, without a URL or port.')
    return value


@app.middleware("http")
async def local_requests(request: Request, call_next):
    allowed_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
    if LAN_ACCESS:
        allowed_hosts.add(LAN_ACCESS['ip'])
    if TAILSCALE_HOST:
        allowed_hosts.add(TAILSCALE_HOST)
    if request.url.hostname not in allowed_hosts:
        return JSONResponse({"detail": "只接受本机请求。"}, status_code=403)
    private_proxy = bool(TAILSCALE_HOST and request.url.hostname == TAILSCALE_HOST)
    if private_proxy:
        # Tailscale Serve authenticates tailnet clients and proxies over loopback.
        # Never infer this trust from forwarded headers or enable a public listener.
        if not mobile_access.local_peer(request.client.host if request.client else ''):
            return JSONResponse({'detail': '私人访问只接受本机代理。'}, status_code=403)
        if request.headers.get('host', '').lower() not in {TAILSCALE_HOST, f'{TAILSCALE_HOST}:443'}:
            return JSONResponse({'detail': '私人访问地址不匹配。'}, status_code=403)
    if LAN_ACCESS and not mobile_access.local_peer(request.client.host if request.client else ''):
        if not mobile_access.authorized(request.headers.get('authorization', ''), LAN_ACCESS):
            return JSONResponse({'detail':'手机访问请使用电脑启动窗口显示的账号和密码。'}, status_code=401,
                                headers={'WWW-Authenticate':'Basic realm="DeepResearch", charset="UTF-8"', 'Cache-Control':'no-store'})
    origin = request.headers.get("origin")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected_origin = f'https://{TAILSCALE_HOST}' if private_proxy else f"{request.url.scheme}://{request.headers.get('host')}"
        if origin and origin != expected_origin:
            return JSONResponse({"detail": "不接受跨站请求。"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "不接受跨站请求。"}, status_code=403)
    account = await run_in_threadpool(accounts.session_user, request.cookies.get(accounts.COOKIE))
    public_path = request.url.path in {'/login', '/register', '/api/auth/login', '/api/auth/register', '/api/health'} or request.url.path.startswith('/static/')
    if not account and not public_path:
        if request.url.path == '/':
            return RedirectResponse('/login', status_code=303)
        return JSONResponse({'detail': '请先登录。'}, status_code=401, headers={'Cache-Control':'no-store'})
    request.state.account = account
    token = accounts.CURRENT_USER.set(account)
    try:
        response = await call_next(request)
    finally:
        accounts.CURRENT_USER.reset(token)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/") or request.url.path in {'/', '/login', '/register'}:
        response.headers["Cache-Control"] = "no-store"
    return response


class SettingsInput(BaseModel):
    base_url: str = Field(max_length=4096)
    model: str = Field(max_length=160)
    api_key: str = Field(default="", max_length=4096)
    search_key: str = Field(default="", max_length=4096)


def require_admin():
    if not accounts.CURRENT_USER.get() or accounts.CURRENT_USER.get()['role'] != 'admin':
        raise HTTPException(403, '此操作仅限管理员。')


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class AccountLimits(BaseModel):
    max_runs: int = Field(default=20, ge=0, le=10000, strict=True)
    max_calls: int = Field(default=400, ge=0, le=100000, strict=True)
    max_tokens: int = Field(default=2000000, ge=0, le=1000000000, strict=True)


class NewAccount(AccountLimits):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=12, max_length=128)


class EditAccount(AccountLimits):
    enabled: bool = Field(strict=True)


class PasswordInput(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


def session_response(request, uid):
    token = accounts.new_session(uid)
    response = JSONResponse({'ok': True})
    response.set_cookie(accounts.COOKIE, token, httponly=True, samesite='strict',
                        secure=request.url.scheme == 'https' or bool(TAILSCALE_HOST and request.url.hostname == TAILSCALE_HOST),
                        max_age=accounts.SESSION_SECONDS, path='/')
    return response


@app.get('/login')
async def login_page():
    return FileResponse(ROOT / 'client' / 'login.html', headers={'Cache-Control':'no-store'})


@app.post('/api/auth/login')
async def login_account(request: Request, payload: LoginInput):
    try:
        account = await run_in_threadpool(accounts.login, payload.username, payload.password, request.client.host if request.client else '')
    except accounts.AccountError as exc:
        raise HTTPException(401, str(exc)) from exc
    accounts.logout(request.cookies.get(accounts.COOKIE))
    return session_response(request, account['id'])


@app.get('/register')
async def register_page():
    return FileResponse(ROOT / 'client' / 'register.html', headers={'Cache-Control':'no-store'})


@app.post('/api/auth/register', status_code=201)
async def register_account(request: Request, payload: LoginInput):
    try:
        await run_in_threadpool(accounts.register, payload.username, payload.password,
                                          request.client.host if request.client else '')
    except accounts.AccountError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({'ok': True, 'message': '注册成功，请使用新账号登录。'}, status_code=201)


@app.post('/api/auth/logout')
async def logout_account(request: Request):
    accounts.logout(request.cookies.get(accounts.COOKIE))
    response = JSONResponse({'ok': True})
    response.delete_cookie(accounts.COOKIE, path='/')
    return response


@app.get('/api/auth/me')
async def my_account():
    return accounts.public(accounts.CURRENT_USER.get())


@app.post('/api/auth/password')
async def change_password(request: Request, payload: PasswordInput):
    try:
        await run_in_threadpool(accounts.change_password, accounts.CURRENT_USER.get()['id'], payload.old_password, payload.new_password)
    except accounts.AccountError as exc:
        raise HTTPException(400, str(exc)) from exc
    return session_response(request, accounts.CURRENT_USER.get()['id'])


@app.get('/api/admin/users')
async def list_accounts():
    require_admin()
    return accounts.all_users()


@app.post('/api/admin/users', status_code=201)
async def add_account(payload: NewAccount):
    require_admin()
    try:
        return await run_in_threadpool(accounts.create_user, payload.username, payload.password,
                                       payload.model_dump(include={'max_runs','max_calls','max_tokens'}))
    except accounts.AccountError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.patch('/api/admin/users/{uid}')
async def edit_account(uid: str, payload: EditAccount):
    require_admin()
    if uid == accounts.CURRENT_USER.get()['id'] and not payload.enabled:
        raise HTTPException(400, '不能停用当前管理员账号。')
    try:
        return accounts.change_user(uid, payload.model_dump(include={'max_runs','max_calls','max_tokens'}), payload.enabled)
    except accounts.AccountError as exc:
        raise HTTPException(400, str(exc)) from exc


class RunInput(BaseModel):
    question: str = Field(min_length=2, max_length=3000)
    depth: str = "standard"
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    document_ids: list[str] = Field(default_factory=list, max_length=5)
    report_language: Literal['zh','en'] = 'zh'
    draft_revision: int | None = Field(default=None, ge=0)


class ComposerDraft(BaseModel):
    question: str = Field(default='', max_length=3000)
    depth: Literal['quick', 'standard', 'deep'] = 'standard'
    report_language: Literal['zh', 'en'] = 'zh'
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    document_ids: list[str] = Field(default_factory=list, max_length=5)
    template: Literal['', 'literature', 'competitor', 'industry', 'technical'] = ''


class PlanDraft(BaseModel):
    objective: str = Field(default='', max_length=1500)
    outline: str = Field(default='', max_length=2000)
    focus: str = Field(default='', max_length=1500)
    exclusions: str = Field(default='', max_length=1000)
    source_preferences: str = Field(default='', max_length=1000)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)


class ComposerDraftInput(BaseModel):
    revision: int = Field(ge=0)
    data: ComposerDraft | None


class PlanDraftInput(BaseModel):
    revision: int = Field(ge=0)
    data: PlanDraft | None


class ConfirmInput(BaseModel):
    plan: ResearchPlan
    budget: BudgetSettings = Field(default_factory=BudgetSettings)


class HistoryInput(BaseModel):
    title: str = Field(max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=10)


class PinInput(BaseModel):
    pinned: bool = Field(strict=True)


class ContinueInput(BaseModel):
    budget: BudgetSettings = Field(default_factory=BudgetSettings)


class TranslationInput(ContinueInput):
    language: Literal['zh','en']


class FollowupInput(ContinueInput):
    prompt: str = Field(min_length=2, max_length=3000)
    mode: str = 'ask'


def require_idle(run_id: str | None = None):
    if active():
        raise HTTPException(409, '你的账号已有任务正在执行，请先停止或等待完成。')
    if sum(not t.done() for t in JOBS.values()) >= 4:
        raise HTTPException(503, '当前有 4 个任务同时运行，请稍后再试。')


def extend_budget(run: dict, extra: BudgetSettings) -> dict:
    # Additional allowance is explicit; accounting and displayed limits remain cumulative.
    return {'max_seconds': int(run['elapsed_seconds']) + extra.max_seconds + 1,
            'max_model_calls': run['usage'].get('model_calls', 0) + extra.max_model_calls,
            'max_tokens': run['usage'].get('input_tokens', 0) + run['usage'].get('output_tokens', 0) + extra.max_tokens}


def render_markdown(text: str) -> str:
    tags = ['p','h1','h2','h3','h4','h5','h6','ul','ol','li','a','strong','em','blockquote','code','pre','hr','br','table','thead','tbody','tr','th','td']
    return bleach.clean(MARKDOWN.render(text), tags=tags, attributes={'a': ['href','title']}, protocols=['http','https'], strip=True)


def active(*, all_users=False) -> bool:
    account = accounts.CURRENT_USER.get()
    return any(not task.done() and (all_users or not account or store.get(rid)['owner_id'] == account['id'])
               for rid, task in JOBS.items())


def dispatch(run_id: str, operation):
    task = asyncio.create_task(operation)
    JOBS[run_id] = task
    def finished(done):
        if JOBS.get(run_id) is done:
            JOBS.pop(run_id, None)
    task.add_done_callback(finished)


def require_run(run_id: str) -> dict:
    run = store.get(run_id)
    account = accounts.CURRENT_USER.get()
    if run is None or (account and run.get('owner_id') != account['id']):
        raise HTTPException(404, "研究不存在。")
    return run


@app.get("/api/health")
async def health():
    return {"ok": True, "app": "open-deep-research-local", "upstream": "langchain-ai/open_deep_research", "snapshot": "1b7d2e8", "running": active(all_users=True), "accounts_enabled": True, "max_concurrent_jobs": 4, "lan_enabled": bool(LAN_ACCESS), "lan_ip": LAN_ACCESS['ip'] if LAN_ACCESS else None, "features": ["plan_confirmation", "research_budget", "followup", "evidence", "documents", "checkpoints", "exports", "history_management", "user_accounts", "private_records", "monthly_quotas", "self_registration", "personal_api", "concurrent_research", "report_translation", "workspace_drafts", "research_templates", "report_navigation", "planning_recovery"]}


@app.get("/api/settings")
async def read_settings():
    return {**public_settings(), 'admin': accounts.CURRENT_USER.get()['role'] == 'admin'}


@app.put("/api/settings")
async def write_settings(payload: SettingsInput):
    if active():
        raise HTTPException(409, "请先停止当前研究，再更改 API 配置。")
    try:
        return save_settings(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/settings/test")
async def test_settings():
    if active():
        raise HTTPException(409, "请等待当前研究结束后再测试连接。")
    current = settings()
    if not current["api_key"] or not current["search_key"]:
        raise HTTPException(400, "请先保存模型 API Key 和 Tavily API Key。")
    from connection_checks import test_connections
    checks = await test_connections(current)
    message = "\n".join(check["message"] for check in checks)
    if not all(check["ok"] for check in checks):
        return JSONResponse({"detail": message, "checks": checks}, status_code=400)
    return {"ok": True, "message": message, "checks": checks}


@app.get("/api/runs")
async def list_runs(q: str = ''):
    return store.list_runs(q[:300])


def owned_unattached_documents(ids):
    result = []
    for doc_id in ids:
        doc = store.document(doc_id)
        if doc and doc['run_id'] is None and doc.get('owner_id') == accounts.CURRENT_USER.get()['id']:
            result.append({k: doc[k] for k in ('id', 'name', 'size', 'created_at')})
    return result


def persist_draft(scope, payload):
    try:
        return store.write_draft(accounts.CURRENT_USER.get()['id'], scope,
                                 payload.data.model_dump() if payload.data is not None else None, payload.revision)
    except store.DraftConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get('/api/drafts/composer')
async def composer_draft():
    item = store.read_draft(accounts.CURRENT_USER.get()['id'], 'composer')
    item['documents'] = owned_unattached_documents(item['data']['document_ids']) if item['data'] else []
    if item['data']:
        item['data']['document_ids'] = [d['id'] for d in item['documents']]
    return item


@app.put('/api/drafts/composer')
async def save_composer_draft(payload: ComposerDraftInput):
    if payload.data and len(owned_unattached_documents(payload.data.document_ids)) != len(payload.data.document_ids):
        raise HTTPException(400, '草稿中的资料不可用，请重新载入草稿。')
    return persist_draft('composer', payload)


@app.get('/api/runs/{run_id}/draft')
async def plan_draft(run_id: str):
    require_run(run_id)
    return store.read_draft(accounts.CURRENT_USER.get()['id'], 'plan:' + run_id)


@app.put('/api/runs/{run_id}/draft')
async def save_plan_draft(run_id: str, payload: PlanDraftInput):
    run = require_run(run_id)
    if run['status'] != 'awaiting_confirmation':
        raise HTTPException(409, '这份计划已不再等待确认，请刷新研究记录。')
    return persist_draft('plan:' + run_id, payload)


@app.post("/api/runs", status_code=201)
async def create_run(payload: RunInput):
    question = payload.question.strip()
    if len(question) < 2:
        raise HTTPException(400, "请输入具体研究问题。")
    if payload.depth not in DEPTHS:
        raise HTTPException(400, "请选择有效的研究深度。")
    require_idle()
    current = settings()
    if not current["api_key"] or not current["search_key"]:
        raise HTTPException(400, "请先在设置中填写模型与 Tavily 搜索密钥。")
    from research_runtime import generate_plan
    if len(set(payload.document_ids)) != len(payload.document_ids):
        raise HTTPException(400, '请勿重复添加同一资料。')
    for doc_id in payload.document_ids:
        doc = store.document(doc_id)
        if not doc or doc['run_id'] is not None or doc.get('owner_id') != accounts.CURRENT_USER.get()['id']:
            raise HTTPException(400, '资料不存在或已经用于其他研究，请重新上传。')
    try:
        accounts.admit(accounts.CURRENT_USER.get()['id'], 'runs')
    except accounts.AccountError as exc:
        raise HTTPException(429, str(exc)) from exc
    run = store.create(question, status="planning", depth=payload.depth, budget=payload.budget.model_dump(), report_language=payload.report_language)
    docs = store.attach_documents(run['id'], payload.document_ids)
    if payload.draft_revision is not None:
        try:
            store.write_draft(accounts.CURRENT_USER.get()['id'], 'composer', None, payload.draft_revision)
        except store.DraftConflict:
            pass  # Preserve any newer draft from another device.
    if docs:
        store.update(run['id'], sources=[{'title': d['name'], 'url': '/api/documents/' + d['id'], 'excerpt': d['text'], 'retrieved_at': d['created_at'], 'kind': 'document'} for d in docs])
    dispatch(run["id"], generate_plan(run["id"], question, current, payload.depth))
    return run


@app.post("/api/runs/{run_id}/confirm")
async def confirm_plan(run_id: str, payload: ConfirmInput):
    run = require_run(run_id)
    if run["status"] != "awaiting_confirmation":
        raise HTTPException(409, "只有待确认的计划可以开始研究，请勿重复确认。")
    require_idle()
    current = settings()
    if not current["api_key"] or not current["search_key"]:
        raise HTTPException(400, "请先在设置中填写模型与 Tavily 搜索密钥。")
    budget = payload.budget.model_dump()
    used = run["usage"]
    if used.get("model_calls", 0) >= budget["max_model_calls"] or used.get("input_tokens", 0) + used.get("output_tokens", 0) >= budget["max_tokens"] or run["elapsed_seconds"] >= budget["max_seconds"]:
        raise HTTPException(400, "预算必须大于规划阶段已经消耗的用量，请调高预算后再确认。")
    from research_runtime import run_research
    plan = payload.plan.model_dump()
    store.begin_phase(run_id, "running", plan=plan, budget=budget, brief=plan_brief(plan), error="")
    store.write_draft(accounts.CURRENT_USER.get()['id'], 'plan:' + run_id, None)
    store.event(run_id, "plan", "用户已确认研究计划与预算")
    dispatch(run_id, run_research(run_id, run["question"], current, run["depth"]))
    return require_run(run_id)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run = require_run(run_id)
    run['report_html'] = render_markdown(run['report'])
    run['partial_report_html'] = render_markdown(store.partial_report(run)) if run['status'] != 'complete' else ''
    run['can_resume'] = bool(run['checkpointed'] and run['operation'] == 'research' and run['status'] not in {'complete', 'running', 'planning'})
    run['can_retry_plan'] = bool(not run['plan'] and not run['checkpointed'] and run['operation'] == 'research' and run['status'] in {'failed', 'interrupted', 'cancelled', 'budget_exceeded'})
    for translation in run['translations']:
        translation['report_html'] = render_markdown(translation['report'])
    for c in run['chats']:
        c['html'] = render_markdown(c['content'])
    return run


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    run = require_run(run_id)
    task = JOBS.get(run_id)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # Covers cancellation before the coroutine starts.
        if store.get(run_id)["status"] in {"running", "planning"}:
            store.finish_phase(run_id, "cancelled")
            store.event(run_id, "cancelled", "研究已停止")
    elif run["status"] == "awaiting_confirmation":
        store.finish_phase(run_id, "cancelled")
        store.event(run_id, "cancelled", "计划已取消，未执行搜索")
    return require_run(run_id)


@app.get("/api/runs/{run_id}/report")
async def download_report(run_id: str, language: Literal['original','zh','en'] = 'original'):
    run = report_for_language(require_run(run_id), language)
    if not run["report"]:
        raise HTTPException(404, "报告尚未生成。")
    return Response(run["report"], media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="research-{run_id[:8]}.md"'})


@app.get("/api/runs/{run_id}/materials")
async def download_materials(run_id: str):
    run = require_run(run_id)
    if not run["sources"] and not run["notes"]:
        raise HTTPException(404, "尚未收集到资料。")
    text = "# 已收集研究资料\n\n> 本文件包含中断前收集的资料，属于未完成的研究，不能作为最终结论。\n\n"
    text += "## 研究问题\n\n" + run["question"] + "\n\n"
    if run["plan"]:
        text += "## 确认计划\n\n" + plan_brief(run["plan"]) + "\n\n"
    text += "## 研究笔记\n\n" + ("\n\n".join(run["notes"]) or "暂无完成的笔记。")
    text += "\n\n## 参考来源\n\n" + "\n".join(f"- {source['title']}：{source['url']}" for source in run["sources"])
    return Response(text, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="materials-{run_id[:8]}.md"'})


@app.post('/api/documents', status_code=201)
async def upload_document(file: UploadFile):
    from research_documents import MAX_BYTES, extract
    try:
        data = await file.read(MAX_BYTES + 1)
        name = Path((file.filename or 'document').replace('\\', '/')).name[:180]
        text = await run_in_threadpool(extract, name, data)
        return store.add_document(name, text, len(data))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, '无法解析该文件，请检查格式、密码或损坏情况。') from exc
    finally:
        await file.close()


@app.get('/api/documents/{doc_id}')
async def read_document(doc_id: str):
    doc = store.document(doc_id)
    if not doc or doc.get('owner_id') != accounts.CURRENT_USER.get()['id']:
        raise HTTPException(404, '资料不存在。')
    return Response(doc['name'] + '\n\n' + doc['text'], media_type='text/plain; charset=utf-8')


@app.patch('/api/runs/{run_id}')
async def edit_history(run_id: str, payload: HistoryInput):
    require_run(run_id)
    if any(len(t.strip()) > 30 or not t.strip() for t in payload.tags):
        raise HTTPException(400, '每个标签需为 1 至 30 个字符。')
    store.update(run_id, title=payload.title.strip(), tags=list(dict.fromkeys(t.strip() for t in payload.tags)))
    return require_run(run_id)


@app.patch('/api/runs/{run_id}/pin')
async def pin_history(run_id: str, payload: PinInput):
    require_run(run_id)
    store.update(run_id, pinned=payload.pinned)
    return {'id': run_id, 'pinned': payload.pinned}


@app.delete('/api/runs/{run_id}')
async def delete_history(run_id: str):
    require_run(run_id)
    if run_id in JOBS and not JOBS[run_id].done():
        raise HTTPException(409, '请先停止该研究，再删除记录。')
    from research_runtime import durable_graph
    async with durable_graph() as graph:
        await graph.checkpointer.adelete_thread(run_id)
    store.delete(run_id)
    return {'ok': True}


@app.post('/api/runs/{run_id}/resume')
async def resume_research(run_id: str, payload: ContinueInput):
    run = require_run(run_id)
    require_idle()
    from research_runtime import resumable, run_research
    if run['operation'] != 'research' or not run['checkpointed'] or run['status'] in {'complete', 'planning', 'awaiting_confirmation'} or not await resumable(run_id):
        raise HTTPException(409, '该研究没有可继续的执行检查点。旧研究可下载资料后新建任务。')
    require_idle()
    current = settings()
    if not current['api_key'] or not current['search_key']:
        raise HTTPException(400, '请先配置模型与搜索密钥。')
    store.begin_phase(run_id, 'running', error='', budget=extend_budget(run, payload.budget))
    dispatch(run_id, run_research(run_id, run['question'], current, run['depth'], resume=True))
    return require_run(run_id)


@app.post('/api/runs/{run_id}/retry-plan')
async def retry_plan(run_id: str, payload: ContinueInput):
    run = require_run(run_id)
    if run['plan'] or run['checkpointed'] or run['operation'] != 'research' or run['status'] not in {'failed', 'interrupted', 'cancelled', 'budget_exceeded'}:
        raise HTTPException(409, '只有尚未生成计划的中断研究可以重试规划。')
    require_idle()
    current = settings()
    if not current['api_key'] or not current['search_key']:
        raise HTTPException(400, '请先配置模型与搜索密钥。')
    from research_runtime import generate_plan
    store.begin_phase(run_id, 'planning', error='', budget=extend_budget(run, payload.budget))
    store.event(run_id, 'plan', '重新生成研究计划，保留原研究记录与累计用量')
    dispatch(run_id, generate_plan(run_id, run['question'], current, run['depth']))
    return require_run(run_id)


@app.post('/api/runs/{run_id}/followup')
async def ask_report(run_id: str, payload: FollowupInput):
    run = require_run(run_id)
    require_idle()
    if not run['report']:
        raise HTTPException(409, '请等待报告完成，再进行追问或修改。')
    if payload.mode not in {'ask', 'revise'} or len(payload.prompt.strip()) < 2:
        raise HTTPException(400, '请选择追问或修改，并填写具体要求。')
    current = settings()
    if not current['api_key'] or not current['search_key']:
        raise HTTPException(400, '请先完成你的模型和搜索 API 配置。')
    from research_runtime import follow_up
    chats = run['chats'] + [{'role': 'user', 'content': payload.prompt.strip(), 'mode': payload.mode, 'time': store.now()}]
    store.begin_phase(run_id, 'running', operation='followup', chats=chats, error='', budget=extend_budget(run, payload.budget))
    dispatch(run_id, follow_up(run_id, payload.prompt.strip(), payload.mode, current))
    return require_run(run_id)


@app.post('/api/runs/{run_id}/translate')
async def translate_report(run_id: str, payload: TranslationInput):
    run = require_run(run_id)
    require_idle()
    if not run['report']:
        raise HTTPException(409, '报告完成后才能翻译。')
    if payload.language == run['report_language'] or any(t['language'] == payload.language for t in run['translations']):
        return await get_run(run_id)
    current = settings()
    if not current['api_key'] or not current['search_key']:
        raise HTTPException(400, '请先完成你的模型和搜索 API 配置。')
    from research_runtime import translate_report as translate
    store.begin_phase(run_id, 'running', operation='translation', error='', budget=extend_budget(run, payload.budget))
    dispatch(run_id, translate(run_id, payload.language, current))
    return await get_run(run_id)


def report_for_language(run, language):
    if language == 'original' or language == run['report_language']:
        return run
    item = next((t for t in run['translations'] if t['language'] == language), None)
    if not item:
        raise HTTPException(409, '请先生成该语言的译文，再下载。')
    return {**run, 'report': item['report']}


@app.get('/api/runs/{run_id}/versions/{version_id}')
async def read_version(run_id: str, version_id: int):
    require_run(run_id)
    version = store.version(run_id, version_id)
    if not version:
        raise HTTPException(404, '报告版本不存在。')
    return {**version, 'report_html': render_markdown(version['report'])}


@app.post('/api/runs/{run_id}/versions/{version_id}/restore')
async def restore_version(run_id: str, version_id: int):
    run = require_run(run_id)
    require_idle()
    version = store.version(run_id, version_id)
    if not version:
        raise HTTPException(404, '报告版本不存在。')
    store.save_report(run_id, version['report'], '恢复历史版本')
    store.event(run_id, 'version', '已恢复历史报告，恢复前的内容仍保留在版本记录中')
    return require_run(run_id)


@app.get('/api/runs/{run_id}/export/{format}')
async def export_report(run_id: str, format: str, language: Literal['original','zh','en'] = 'original'):
    run = report_for_language(require_run(run_id), language)
    if format not in {'pdf', 'docx'}:
        raise HTTPException(400, '支持 Word 与 PDF 导出。')
    if not run['report']:
        raise HTTPException(409, '报告尚未生成，可先下载已收集资料。')
    from research_export import export
    data = await run_in_threadpool(export, run, format)
    mime = 'application/pdf' if format == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return Response(data, media_type=mime, headers={'Content-Disposition': f'attachment; filename="research-{run_id[:8]}.{format}"'})


app.mount("/static", StaticFiles(directory=ROOT / "client"), name="static")


@app.get("/")
async def index():
    return FileResponse(ROOT / "client" / "index.html")


def main():
    global LAN_ACCESS, TAILSCALE_HOST
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument('--lan-ip', help='Enable password-protected LAN access on this private IPv4 address')
    parser.add_argument('--tailscale-host', help='Exact ts.net DNS name served by Tailscale Serve; keeps the app on loopback')
    args = parser.parse_args()
    if args.tailscale_host and args.lan_ip:
        parser.error('--tailscale-host cannot be combined with --lan-ip')
    try:
        TAILSCALE_HOST = tailscale_hostname(args.tailscale_host) if args.tailscale_host else None
    except ValueError as exc:
        parser.error(str(exc))
    if args.lan_ip:
        try:
            LAN_ACCESS = {**mobile_access.credentials(), 'ip': mobile_access.validate_ip(args.lan_ip)}
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    else:
        LAN_ACCESS = None
    bind_host = '0.0.0.0' if LAN_ACCESS else '127.0.0.1'
    port = args.port
    for candidate in range(port, port + 20):
        try:
            with socket.socket() as probe:
                probe.bind((bind_host, candidate))
            port = candidate
            break
        except OSError:
            continue
    else:
        raise SystemExit("No available local port. Close another server and try again.")
    url = f"http://127.0.0.1:{port}"
    print(f'First administrator credentials (if not yet changed): {store.DB_FILE.parent / "admin-first-login.txt"}', flush=True)
    print(f"\nOpen Deep Research: {url}\nKeep this window open. Press Ctrl+C to stop.\n", flush=True)
    if LAN_ACCESS:
        mobile_access.show_instructions(LAN_ACCESS['ip'], port, LAN_ACCESS)
    if args.open_browser:
        threading.Timer(2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=bind_host, port=port, log_level="info", proxy_headers=False)


if __name__ == "__main__":
    main()
