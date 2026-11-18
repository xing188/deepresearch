import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import local_config
import research_store as store
import server
import user_accounts as accounts
from research_budget import BudgetGuard, BudgetExceeded


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(store, 'DB_FILE', Path(self.folder.name)/'research.sqlite3')
        self.envpatch = patch.object(local_config, 'ENV_FILE', Path(self.folder.name)/'.env')
        self.dbpatch.start(); self.envpatch.start()
        store.initialize()
        self.legacy = store.create('管理员历史研究', status='complete')
        self.doc = store.add_document('private.txt', 'private administrator document', 40)
        self.client = TestClient(server.app).__enter__()
        text=(Path(self.folder.name)/'admin-first-login.txt').read_text(encoding='utf-8')
        self.password=text.split('密码：')[1].strip()
        self.assertEqual(self.client.post('/api/auth/login',json={'username':'admin','password':self.password}).status_code,200)
        self.admin=self.client.get('/api/auth/me').json()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.envpatch.stop(); self.dbpatch.stop(); self.folder.cleanup()

    def member(self, name='friend', **limits):
        payload={'username':name,'password':'member-password-123', 'max_runs':2,'max_calls':3,'max_tokens':10000,**limits}
        result=self.client.post('/api/admin/users',json=payload)
        self.assertEqual(result.status_code,201,result.text)
        member=TestClient(server.app)
        self.assertEqual(member.post('/api/auth/login',json={'username':name,'password':payload['password']}).status_code,200)
        return member,result.json()

    def test_migration_login_logout_and_access_control(self):
        guest=TestClient(server.app)
        self.assertEqual(guest.get('/',follow_redirects=False).status_code,303)
        self.assertEqual(guest.get('/api/runs').status_code,401)
        self.assertEqual(guest.post('/api/admin/users',json={}).status_code,401)
        response=guest.post('/api/auth/login',json={'username':'admin','password':'incorrect'})
        self.assertEqual(response.status_code,401)
        self.assertEqual(self.client.get('/api/runs').json()[0]['id'],self.legacy['id'])
        self.assertEqual(store.get(self.legacy['id'])['owner_id'],self.admin['id'])
        with store.connect() as db:
            row=db.execute('SELECT * FROM users WHERE id=?',(self.admin['id'],)).fetchone()
            self.assertNotIn(self.password,row['password_hash'])
            self.assertNotEqual(db.execute('SELECT hash FROM sessions').fetchone()['hash'],self.client.cookies.get(accounts.COOKIE))
        self.assertEqual(self.client.post('/api/auth/logout').status_code,200)
        self.assertEqual(self.client.get('/api/runs').status_code,401)

    def test_registration_requires_login_before_access(self):
        guest=TestClient(server.app)
        payload={'username':'newmember','password':'new-member-password-123','role':'admin'}
        self.assertEqual(guest.get('/register').status_code,200)
        self.assertEqual(guest.post('/api/auth/register',json={**payload,'password':'short'}).status_code,400)
        response=guest.post('/api/auth/register',json=payload)
        self.assertEqual(response.status_code,201,response.text)
        self.assertNotIn('set-cookie',response.headers)
        self.assertEqual(guest.get('/api/auth/me').status_code,401)
        self.assertEqual(guest.get('/api/runs').status_code,401)
        self.assertEqual(guest.get('/',follow_redirects=False).headers['location'],'/login')
        self.assertEqual(guest.post('/api/auth/register',json=payload).status_code,400)
        self.assertEqual(guest.post('/api/auth/login',json={**payload,'password':'incorrect'}).status_code,401)
        self.assertEqual(guest.post('/api/auth/login',json=payload).status_code,200)
        self.assertEqual(guest.get('/api/auth/me').json()['role'],'member')
        self.assertEqual(guest.get('/api/runs').json(),[])

    def test_isolation_all_run_routes_documents_and_settings(self):
        member,u=self.member()
        self.assertEqual(member.get('/api/runs').json(),[])
        rid=self.legacy['id']
        for method,path,payload in [
            ('GET',f'/api/runs/{rid}',None), ('GET',f'/api/runs/{rid}/report',None),
            ('GET',f'/api/runs/{rid}/materials',None),('GET',f'/api/runs/{rid}/export/pdf',None),
            ('GET',f'/api/runs/{rid}/versions/1',None),('POST',f'/api/runs/{rid}/versions/1/restore',{}),
            ('PATCH',f'/api/runs/{rid}',{'title':'attack','tags':[]}),('PATCH',f'/api/runs/{rid}/pin',{'pinned':True}),
            ('DELETE',f'/api/runs/{rid}',None),('POST',f'/api/runs/{rid}/confirm',{'plan':{'objective':'valid objective','outline':['one']}}),
            ('POST',f'/api/runs/{rid}/cancel',{}),('POST',f'/api/runs/{rid}/resume',{}),
            ('POST',f'/api/runs/{rid}/followup',{'prompt':'explain report'})]:
            response=member.request(method,path,json=payload)
            self.assertEqual(response.status_code,404,(path,response.text))
        self.assertEqual(member.get('/api/documents/'+self.doc['id']).status_code,404)
        self.assertEqual(member.get('/api/admin/users').status_code,403)
        self.assertEqual(member.post('/api/admin/users',json={'username':'hacker','password':'password-for-hacker'}).status_code,403)
        self.assertEqual(member.put('/api/settings',json={'base_url':'https://example.test','model':'x'}).status_code,200)
        self.assertEqual(member.get('/api/settings').json()['api_key_set'],False)
        self.assertNotEqual(self.client.get('/api/settings').json()['base_url'],'https://example.test')
        self.assertEqual(member.post('/api/settings/test').status_code,400)
        local_config.save_settings({'api_key':'test-key','search_key':'test-key'})
        stolen=member.post('/api/runs',json={'question':'try attach stolen document','document_ids':[self.doc['id']]})
        self.assertEqual(stolen.status_code,400)
        upload=member.post('/api/documents',files={'file':('mine.txt',b'my private reference','text/plain')})
        self.assertEqual(upload.status_code,201,upload.text)
        docid=upload.json()['id']
        self.assertEqual(member.get('/api/documents/'+docid).status_code,200)
        self.assertEqual(self.client.get('/api/documents/'+docid).status_code,404)

    def test_monthly_quota_persists_on_delete_restart_and_month_rollover(self):
        member,u=self.member()
        account=accounts.user(u['id'])
        async def fake_plan(rid,*args):
            store.finish_phase(rid,'awaiting_confirmation',plan={'objective':'objective','outline':['one']})
        local_config.save_settings({'api_key':'test-key','search_key':'test-key'})
        member.put('/api/settings',json={'base_url':'https://mock.test/v1','model':'test','api_key':'member-key','search_key':'member-search'})
        with patch('research_runtime.generate_plan',fake_plan):
            r=member.post('/api/runs',json={'question':'member owned research'})
            self.assertEqual(r.status_code,201,r.text)
            rid=r.json()['id']
            self.assertEqual(store.get(rid)['owner_id'],u['id'])
            self.assertEqual(self.client.get('/api/runs/'+rid).status_code,404)
            self.assertEqual(member.delete('/api/runs/'+rid).status_code,200)
        accounts.admit(u['id'],'runs')
        denied=member.post('/api/runs',json={'question':'over monthly quota'})
        self.assertEqual(denied.status_code,429,denied.text)
        accounts.initialize()
        self.assertEqual(accounts.public(account)['usage']['runs'],2)
        with patch.object(accounts,'month',return_value='2099-01'):
            accounts.admit(u['id'],'runs')
            self.assertEqual(accounts.public(account)['usage']['runs'],1)
        accounts.record_tokens(u['id'],accounts.month(),10000)
        with self.assertRaises(accounts.AccountError):accounts.admit(u['id'],'calls')

    def test_atomic_call_limit_and_guard_accounting(self):
        member,u=self.member(max_calls=3)
        token=accounts.CURRENT_USER.set(accounts.user(u['id']))
        try:run=store.create('guard quota',budget={'max_seconds':60,'max_model_calls':20,'max_tokens':100000})
        finally:accounts.CURRENT_USER.reset(token)
        async def execute_calls():
            guard=BudgetGuard(run['id'])
            result=SimpleNamespace(generations=[SimpleNamespace(message=SimpleNamespace(usage_metadata={'input_tokens':25,'output_tokens':10}))])
            guard.before_call();guard.after_call(result)
            guard.before_call();guard.after_call() # cancelled/failed calls still count
            guard.before_call();guard.after_call(result)
            with self.assertRaises(BudgetExceeded):guard.before_call()
        asyncio.run(execute_calls())
        self.assertEqual(accounts.public(accounts.user(u['id']))['usage'],{'runs':0,'calls':3,'tokens':70})
        # Independent connections cannot race past a quota.
        _,v=self.member('second',max_calls=4)
        def admit(_):
            try:accounts.admit(v['id'],'calls');return 1
            except accounts.AccountError:return 0
        with ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(admit,range(15))),4)

    def test_password_rotation_disables_sessions_and_login_rate_limit(self):
        member,u=self.member()
        other=TestClient(server.app)
        other.post('/api/auth/login',json={'username':'friend','password':'member-password-123'})
        response=member.post('/api/auth/password',json={'old_password':'member-password-123','new_password':'new-long-password-456'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(member.get('/api/auth/me').status_code,200)
        self.assertEqual(other.get('/api/auth/me').status_code,401)
        disabled=self.client.patch('/api/admin/users/'+u['id'],json={'enabled':False,'max_runs':2,'max_calls':3,'max_tokens':10000})
        self.assertEqual(disabled.status_code,200)
        self.assertEqual(member.get('/api/auth/me').status_code,401)
        with self.assertRaises(accounts.AccountError):accounts.admit(u['id'],'calls')
        self.assertEqual(self.client.patch('/api/admin/users/'+self.admin['id'],json={'enabled':False,'max_runs':2,'max_calls':3,'max_tokens':10000}).status_code,400)
        for i in range(8):other.post('/api/auth/login',json={'username':'nonexistent','password':'bad'})
        self.assertIn('15 分钟',other.post('/api/auth/login',json={'username':'nonexistent','password':'bad'}).json()['detail'])

    def test_private_cookie_https_and_cross_site_rejection(self):
        with patch.object(server,'TAILSCALE_HOST','research.example.ts.net'):
            proxy=TestClient(server.app,client=('127.0.0.1',4000),base_url='http://research.example.ts.net')
            response=proxy.post('/api/auth/login',json={'username':'admin','password':self.password},headers={'Origin':'https://research.example.ts.net'})
            self.assertEqual(response.status_code,200,response.text)
            cookie=response.headers['set-cookie']
            for flag in ('Secure','HttpOnly','SameSite=strict'):self.assertIn(flag,cookie)
            self.assertEqual(proxy.get('/api/runs').status_code,401) # secure cookie not sent on HTTP
            proxy.base_url='https://research.example.ts.net'
            self.assertEqual(proxy.get('/api/runs').status_code,200)
            self.assertEqual(proxy.post('/api/auth/logout',headers={'Origin':'https://evil.example'}).status_code,403)
            self.assertEqual(proxy.post('/api/auth/logout',headers={'Origin':'https://research.example.ts.net'}).status_code,200)


if __name__=='__main__':unittest.main()
