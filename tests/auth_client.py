"""Authenticated client for legacy feature tests; auth itself has separate tests."""
from fastapi.testclient import TestClient as BaseClient
import user_accounts as accounts
import research_store as store


class TestClient(BaseClient):
    def __enter__(self):
        super().__enter__()
        with store.connect() as db:
            account = dict(db.execute("SELECT * FROM users WHERE role='admin' LIMIT 1").fetchone())
        self.cookies.set(accounts.COOKIE, accounts.new_session(account['id']))
        self.account_token = accounts.CURRENT_USER.set(account)
        return self

    def __exit__(self, *args):
        accounts.CURRENT_USER.reset(self.account_token)
        return super().__exit__(*args)
