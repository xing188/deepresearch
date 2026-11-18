"""Per-run admission limits and cancellation that upstream retries cannot bypass."""

import asyncio
import time
from contextvars import ContextVar

from pydantic import BaseModel, Field

import research_store as store


class BudgetSettings(BaseModel):
    max_seconds: int = Field(default=600, ge=60, le=3600, strict=True)
    max_model_calls: int = Field(default=40, ge=3, le=200, strict=True)
    max_tokens: int = Field(default=250000, ge=10000, le=2000000, strict=True)


class BudgetExceeded(Exception):
    pass


CURRENT_BUDGET: ContextVar = ContextVar("research_budget", default=None)


class BudgetGuard:
    def __init__(self, run_id: str):
        run = store.get(run_id)
        self.run_id = run_id
        self.owner_id = run.get('owner_id')
        self.accounting_periods = {}
        self.limits = run["budget"] or BudgetSettings().model_dump()
        self.usage = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "in_flight": 0, "usage_incomplete": False, **run["usage"]}
        self.deadline = time.monotonic() + self.limits["max_seconds"] - run["elapsed_seconds"]
        self.exhausted = asyncio.Event()
        self.reason = ""
        self.closed = False
        self.requests = set()

    def stop(self, reason: str):
        if not self.reason:
            self.reason = reason
        self.exhausted.set()
        raise BudgetExceeded(self.reason)

    def check(self):
        if self.closed:
            raise BudgetExceeded(self.reason or "研究已经停止。")
        if self.reason:
            raise BudgetExceeded(self.reason)
        if time.monotonic() >= self.deadline:
            self.stop(f"已达到运行时间上限（{self.limits['max_seconds'] // 60} 分钟），研究已停止。")
        if self.usage["input_tokens"] + self.usage["output_tokens"] >= self.limits["max_tokens"]:
            self.stop(f"已达到 token 预算（{self.limits['max_tokens']:,}），研究已停止。")

    def before_call(self):
        # No await between checking and incrementing: concurrent asyncio tasks
        # cannot admit requests beyond the model call limit.
        self.check()
        if self.usage["model_calls"] >= self.limits["max_model_calls"]:
            self.stop(f"已达到模型调用上限（{self.limits['max_model_calls']} 次），研究已停止。")
        import user_accounts
        try:
            period = user_accounts.admit(self.owner_id, 'calls')
        except user_accounts.AccountError as exc:
            self.stop(str(exc))
        self.accounting_periods[asyncio.current_task()] = period
        self.usage["model_calls"] += 1
        self.usage["in_flight"] += 1
        self.requests.add(asyncio.current_task())
        self.persist()

    def after_call(self, result=None):
        import user_accounts
        period = self.accounting_periods.pop(asyncio.current_task(), None)
        self.requests.discard(asyncio.current_task())
        self.usage["in_flight"] = max(0, self.usage["in_flight"] - 1)
        token_usage = None
        if result is not None:
            for generation in result.generations:
                usage = getattr(generation.message, "usage_metadata", None)
                if usage:
                    token_usage = {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}
                    break
            if token_usage is None:
                raw = (result.llm_output or {}).get("token_usage")
                if raw:
                    token_usage = {"input_tokens": raw.get("prompt_tokens", 0), "output_tokens": raw.get("completion_tokens", 0)}
        if token_usage is not None:
            self.usage["input_tokens"] += token_usage["input_tokens"]
            self.usage["output_tokens"] += token_usage["output_tokens"]
            if period:
                user_accounts.record_tokens(self.owner_id, period, token_usage['input_tokens'] + token_usage['output_tokens'])
        else:
            self.usage["usage_incomplete"] = True
        self.persist()
        if result is not None and token_usage is None:
            self.stop("模型接口未返回 token 用量，无法继续按 token 预算执行。已停止并保留资料。")
        if result is not None:
            self.check()

    def persist(self):
        store.update(self.run_id, usage=self.usage)

    async def execute(self, operation):
        token = CURRENT_BUDGET.set(self)
        work = asyncio.create_task(operation())
        watcher = asyncio.create_task(self.exhausted.wait())
        try:
            self.check()
            completed, _ = await asyncio.wait({work, watcher}, timeout=max(0, self.deadline - time.monotonic()), return_when=asyncio.FIRST_COMPLETED)
            if self.exhausted.is_set():
                raise BudgetExceeded(self.reason)
            if work in completed:
                return await work
            self.stop(f"已达到运行时间上限（{self.limits['max_seconds'] // 60} 分钟），研究已停止。")
        finally:
            self.closed = True
            pending = {work, watcher, *self.requests}
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            CURRENT_BUDGET.reset(token)
