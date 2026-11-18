import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import connection_checks as checks


class ConnectionTests(unittest.TestCase):
    def test_connection_failure_preserves_successful_service(self):
        current = {"api_key": "private-model-key", "search_key": "private-search-key"}
        with patch.object(checks, "check_model", AsyncMock(side_effect=httpx.ConnectError("Connection error"))), patch.object(checks, "check_search", AsyncMock(return_value=None)):
            result = asyncio.run(checks.test_connections(current))
        self.assertFalse(result[0]["ok"])
        self.assertIn("联网权限", result[0]["message"])
        self.assertTrue(result[1]["ok"])
        self.assertIn("Tavily", result[1]["message"])

    def test_nested_network_permission_is_identified(self):
        error = httpx.ConnectError("All connection attempts failed")
        error.__cause__ = ExceptionGroup("connection attempts", [PermissionError("blocked")])
        result = checks.describe_failure(error, "模型", {})
        self.assertIn("start.bat", result)

    def test_authentication_and_redaction(self):
        class AuthError(Exception):
            status_code = 401
        result = checks.describe_failure(AuthError("bad key"), "模型", {})
        self.assertIn("认证失败", result)
        result = checks.describe_failure(ValueError("error secret-key-content"), "Tavily", {"search_key": "secret-key-content"})
        self.assertNotIn("secret-key-content", result)


if __name__ == "__main__":
    unittest.main()
