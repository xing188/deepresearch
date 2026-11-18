import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auth_client import TestClient
import mobile_access
import research_store as store
import server


class MobileTests(unittest.TestCase):
    def test_password_persists_and_is_checked_exactly(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(mobile_access, 'ACCESS_FILE', Path(folder)/'access.json'):
            saved = mobile_access.credentials()
            self.assertEqual(saved, mobile_access.credentials())
            self.assertGreaterEqual(len(saved['password']), 16)
            header = 'Basic ' + base64.b64encode((saved['username']+':'+saved['password']).encode()).decode()
            self.assertTrue(mobile_access.authorized(header, saved))
            for bad in ('', 'Basic not-base64', 'Bearer token', header[:-2]+'xx'):
                self.assertFalse(mobile_access.authorized(bad, saved))

    def test_remote_requires_password_and_host_cannot_bypass_auth(self):
        access = {'ip':'192.168.1.50', 'username':'research', 'password':'test-mobile-password'}
        auth = 'Basic ' + base64.b64encode(b'research:test-mobile-password').decode()
        with tempfile.TemporaryDirectory() as folder, patch.object(store,'DB_FILE',Path(folder)/'test.sqlite3'), patch.object(server,'LAN_ACCESS',access):
            with TestClient(server.app, client=('192.168.1.80',3000), base_url='http://192.168.1.50:8000') as client:
                for path in ('/', '/api/runs', '/api/settings','/docs'):
                    self.assertEqual(client.get(path).status_code,401)
                self.assertIn('Basic',client.get('/').headers['www-authenticate'])
                self.assertEqual(client.get('/api/health',headers={'Host':'127.0.0.1:8000'}).status_code,401)
                self.assertEqual(client.get('/api/health',headers={'X-Forwarded-For':'127.0.0.1'}).status_code,401)
                result=client.get('/api/health',headers={'Authorization':auth})
                self.assertEqual(result.status_code,200)
                self.assertTrue(result.json()['lan_enabled'])
                self.assertNotIn(access['password'],result.text)
                self.assertEqual(client.get('/',headers={'Authorization':auth,'Host':'evil.example'}).status_code,403)
                self.assertEqual(client.patch('/api/runs/missing/pin',json={'pinned':True},headers={'Authorization':auth,'Origin':'http://evil.example'}).status_code,403)
            with TestClient(server.app,client=('127.0.0.1',3000)) as local:
                self.assertEqual(local.get('/api/health').status_code,200)
            with patch.object(server,'LAN_ACCESS',None), TestClient(server.app,base_url='http://192.168.1.50:8000') as disabled:
                self.assertEqual(disabled.get('/').status_code,403)


if __name__ == '__main__':
    unittest.main()
