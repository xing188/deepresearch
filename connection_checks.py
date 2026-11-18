"""Probe both configured services without exposing credentials."""

import asyncio
import ssl
from pydantic import BaseModel


def describe_failure(exc: Exception, label: str, current: dict) -> str:
    chain, pending, seen = [], [exc], set()
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        chain.append(error)
        if error.__cause__:
            pending.append(error.__cause__)
        if error.__context__:
            pending.append(error.__context__)
        pending.extend(getattr(error, "exceptions", []))
    status = getattr(exc, "status_code", None)
    if status == 401:
        explanation = "密钥认证失败，请核对该服务的 API Key。"
    elif status == 402:
        explanation = "服务余额或额度不足，请检查账户。"
    elif status == 403:
        explanation = "服务拒绝访问，请检查账户权限或访问限制。"
    elif status == 404:
        explanation = "接口地址或模型不存在，请核对 API Base URL 和模型 ID。"
    elif status == 429:
        explanation = "请求受到限流或额度限制，请检查账户并稍后重试。"
    elif any(isinstance(error, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(error) for error in chain):
        explanation = "TLS 证书验证失败，请检查系统时间、证书或 HTTPS 代理。"
    elif any(isinstance(error, PermissionError) or getattr(error, "winerror", None) == 10013 for error in chain):
        explanation = "当前服务进程的联网权限被限制，请在本机双击 start.bat 启动；若仍失败，请检查防火墙。"
    elif any(isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower() for error in chain):
        explanation = "连接超时，请检查该服务的网络访问、代理设置，或稍后重试。"
    elif any("connect" in type(error).__name__.lower() for error in chain):
        explanation = "无法建立连接，请检查服务进程的联网权限、代理和防火墙；这不代表密钥错误。"
    else:
        explanation = str(exc) or type(exc).__name__
    for name in ("api_key", "search_key"):
        if current.get(name):
            explanation = explanation.replace(current[name], "[redacted]")
    return f"{label}：{explanation[:1200]}"


async def check_model(current: dict):
    from research_runtime import CompatibleChatModel

    class ConnectionResult(BaseModel):
        status: str

    model = CompatibleChatModel(model=current["model"], api_key=current["api_key"], base_url=current["base_url"], max_tokens=128, timeout=25, max_retries=0, use_responses_api=False, stream_usage=False)
    result = await model.with_structured_output(ConnectionResult).ainvoke("Return status='ok' using the ConnectionResult tool.")
    if result is None or not result.status:
        raise ValueError("模型未返回结构化工具输出，请使用支持 Function Calling 的模型。")


async def check_search(current: dict):
    from tavily import AsyncTavilyClient
    result = await AsyncTavilyClient(api_key=current["search_key"]).search("LangChain Open Deep Research", max_results=1)
    if not result.get("results"):
        raise ValueError("未返回搜索结果。")


async def test_connections(current: dict) -> list[dict]:
    async def probe(service, label, operation):
        try:
            async with asyncio.timeout(35):
                await operation(current)
            return {"service": service, "ok": True, "message": f"{label}：连接成功。"}
        except Exception as exc:
            return {"service": service, "ok": False, "message": describe_failure(exc, label, current)}
    return await asyncio.gather(
        probe("model", "模型接口（含工具调用）", check_model),
        probe("tavily", "Tavily 搜索", check_search),
    )
