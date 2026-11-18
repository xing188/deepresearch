import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient as GuestClient
from auth_client import TestClient
import local_config
import research_runtime as runtime
import research_store as store
import server


class ProductivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = patch.object(store, 'DB_FILE', Path(self.temp.name) / 'runs.sqlite3')
        self.env = patch.object(local_config, 'ENV_FILE', Path(self.temp.name) / '.env')
        self.db.start(); self.env.start()
        self.client = TestClient(server.app).__enter__()
        local_config.save_settings({'api_key':'mock-key', 'search_key':'mock-search'})

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.env.stop(); self.db.stop(); self.temp.cleanup()

    def member(self):
        member = GuestClient(server.app)
        data = {'username':'other','password':'other-password-123'}
        self.assertEqual(member.post('/api/auth/register',json=data).status_code,201)
        self.assertEqual(member.post('/api/auth/login',json=data).status_code,200)
        return member

    def test_drafts_are_private_persistent_and_use_atomic_revisions(self):
        path = '/api/drafts/composer'
        self.assertEqual(GuestClient(server.app).get(path).status_code,401)
        doc = self.client.post('/api/documents',files={'file':('notes.txt',b'source text')}).json()
        data = {'question':'My unfinished research','template':'technical','document_ids':[doc['id']], 'api_key':'must-not-be-stored'}
        first = self.client.put(path,json={'revision':0,'data':data})
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()['revision'],1)
        self.assertNotIn('api_key',first.json()['data'])
        store.initialize()
        saved = self.client.get(path).json()
        self.assertEqual(saved['data']['question'],data['question'])
        self.assertEqual(saved['documents'][0]['id'],doc['id'])
        self.assertNotIn('text',saved['documents'][0])
        stale = self.client.put(path,json={'revision':0,'data':{'question':'overwrite'}})
        self.assertEqual(stale.status_code,409)
        member = self.member()
        self.assertIsNone(member.get(path).json()['data'])
        self.assertEqual(member.put(path,json={'revision':0,'data':data}).status_code,400)
        self.assertEqual(member.put(path,json={'revision':0,'data':{'question':'Member draft'}}).status_code,200)
        self.assertEqual(self.client.get(path).json()['data']['question'],data['question'])
        cleared = self.client.put(path,json={'revision':1,'data':None})
        self.assertEqual(cleared.json()['revision'],2)
        self.assertEqual(self.client.put(path,json={'revision':1,'data':data}).status_code,409)
        self.assertIsNone(self.client.get(path).json()['data'])

    def test_submission_clears_only_matching_draft_and_failure_keeps_it(self):
        path = '/api/drafts/composer'
        draft = self.client.put(path,json={'revision':0,'data':{'question':'First draft'}}).json()
        self.assertEqual(self.client.post('/api/runs',json={'question':'x','draft_revision':draft['revision']}).status_code,422)
        self.assertIsNotNone(self.client.get(path).json()['data'])
        async def plan(rid,*args): store.finish_phase(rid,'awaiting_confirmation',plan={'objective':'Goal','outline':['Evidence']})
        with patch.object(runtime,'generate_plan',plan):
            # A newer draft from another device must survive this old submission.
            self.client.put(path,json={'revision':1,'data':{'question':'Newer device draft'}})
            response = self.client.post('/api/runs',json={'question':'First draft','draft_revision':1})
            self.assertEqual(response.status_code,201,response.text)
            self.assertEqual(self.client.get(path).json()['data']['question'],'Newer device draft')
            self.client.post('/api/runs/'+response.json()['id']+'/cancel')
            response = self.client.post('/api/runs',json={'question':'Newer device draft','draft_revision':2})
            self.assertEqual(response.status_code,201,response.text)
            self.assertIsNone(self.client.get(path).json()['data'])

    def test_plan_edits_restore_validate_ownership_and_clear_on_confirm(self):
        run = store.create('Plan draft',status='awaiting_confirmation')
        store.update(run['id'],plan={'objective':'Original','outline':['Evidence']})
        path = '/api/runs/'+run['id']+'/draft'
        data = {'objective':'','outline':'','focus':'Incomplete edits may be saved','budget':{'max_seconds':120,'max_model_calls':10,'max_tokens':20000}}
        self.assertEqual(self.client.put(path,json={'revision':0,'data':data}).status_code,200)
        self.assertEqual(self.client.get(path).json()['data']['objective'],'')
        member = self.member()
        self.assertEqual(member.get(path).status_code,404)
        self.assertEqual(member.put(path,json={'revision':1,'data':data}).status_code,404)
        store.initialize()
        self.assertEqual(self.client.get(path).json()['data']['focus'],data['focus'])
        async def research(rid,*args): store.finish_phase(rid,'complete',report='# Completed')
        with patch.object(runtime,'run_research',research):
            result = self.client.post('/api/runs/'+run['id']+'/confirm',json={'plan':{'objective':'Revised','outline':['Evidence']}})
            self.assertEqual(result.status_code,200,result.text)
        self.assertIsNone(self.client.get(path).json()['data'])
        self.assertEqual(self.client.put(path,json={'revision':1,'data':data}).status_code,409)
        self.assertEqual(store.get(run['id'])['plan']['objective'],'Revised')

    def test_retry_planning_reuses_record_and_cumulative_usage(self):
        run = store.create('Interrupted planning',status='interrupted',budget={'max_seconds':60,'max_model_calls':10,'max_tokens':10000})
        store.update(run['id'],usage={'model_calls':2,'input_tokens':50,'output_tokens':20},elapsed_seconds=5)
        path = '/api/runs/'+run['id']
        self.assertTrue(self.client.get(path).json()['can_retry_plan'])
        member = self.member()
        self.assertEqual(member.post(path+'/retry-plan',json={}).status_code,404)
        async def plan(rid,*args):
            store.finish_phase(rid,'awaiting_confirmation',plan={'objective':'Recovered','outline':['Evidence']})
        with patch.object(runtime,'generate_plan',plan):
            result = self.client.post(path+'/retry-plan',json={'budget':{'max_seconds':60,'max_model_calls':3,'max_tokens':10000}})
            self.assertEqual(result.status_code,200,result.text)
            for _ in range(50):
                if store.get(run['id'])['status']=='awaiting_confirmation': break
                time.sleep(.01)
        recovered = self.client.get(path).json()
        self.assertEqual(recovered['id'],run['id'])
        self.assertEqual(recovered['budget']['max_model_calls'],5)
        self.assertEqual(recovered['usage']['model_calls'],2)
        self.assertFalse(recovered['can_retry_plan'])
        self.assertEqual(self.client.post(path+'/retry-plan',json={}).status_code,409)


if __name__ == '__main__': unittest.main()
