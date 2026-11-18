import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auth_client import TestClient

import research_store as store
import server


class PrivateAccessTests(unittest.TestCase):
    def test_only_exact_private_hostname_is_accepted(self):
        self.assertEqual(server.tailscale_hostname('Research.Example.ts.net'), 'research.example.ts.net')
        for name in ('example.com', 'https://research.example.ts.net', 'research.example.ts.net:443',
                     '*.example.ts.net', 'research.example.ts.net/evil', '-bad.example.ts.net', 'ts.net'):
            with self.assertRaises(ValueError):
                server.tailscale_hostname(name)

    def test_private_proxy_keeps_origin_and_peer_checks(self):
        host = 'research.example.ts.net'
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(store, 'DB_FILE', Path(folder) / 'research.sqlite3'), \
             patch.object(server, 'TAILSCALE_HOST', host):
            # The backend HTTP hop stays on loopback; the browser origin is HTTPS.
            with TestClient(server.app, client=('127.0.0.1', 4000), base_url=f'http://{host}') as client:
                self.assertEqual(client.get('/').status_code, 200)
                accepted = client.patch('/api/runs/missing/pin', json={'pinned': True}, headers={'Origin': f'https://{host}'})
                self.assertEqual(accepted.status_code, 404)
                for origin in ('https://evil.example', f'http://{host}', f'https://{host}.evil.example'):
                    self.assertEqual(client.patch('/api/runs/missing/pin', json={'pinned': True}, headers={'Origin': origin}).status_code, 403)
                self.assertEqual(client.patch('/api/runs/missing/pin', json={'pinned': True}, headers={'Origin': f'https://{host}', 'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
                self.assertEqual(client.get('/', headers={'Host': 'evil.example'}).status_code, 403)
                self.assertEqual(client.get('/', headers={'Host': f'{host}:8000'}).status_code, 403)
                self.assertEqual(client.get('http://127.0.0.1:8000/api/health').status_code, 200)
            with TestClient(server.app, client=('100.100.100.10', 4000), base_url=f'http://{host}') as client:
                self.assertEqual(client.get('/', headers={'X-Forwarded-For': '127.0.0.1', 'X-Forwarded-Proto': 'https'}).status_code, 403)
            with patch.object(server, 'TAILSCALE_HOST', None), TestClient(server.app, base_url=f'http://{host}') as client:
                self.assertEqual(client.get('/').status_code, 403)


if __name__ == '__main__':
    unittest.main()
