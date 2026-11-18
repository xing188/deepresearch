import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient as RawClient
from auth_client import TestClient
import local_config
import research_runtime as runtime
import research_store as store
import server
import user_accounts as accounts


class MultiplayerTests(unittest.TestCase):
    def test_long_translation_keeps_code_and_table_blocks_intact(self):
        source = '# Title\n\n' + ('Paragraph. '*60) + '\n\n```python\nprint("hello")\n\nprint("world")\n```\n\n| Name | Value |\n|---|---|\n| A | B |\n\n[Source](https://example.com/evidence)'
        chunks = runtime.translation_chunks(source, limit=200)
        self.assertGreater(len(chunks),1)
        self.assertEqual(''.join(chunks),source)
        self.assertTrue(any('```python\nprint("hello")\n\nprint("world")\n```' in c for c in chunks))
        self.assertTrue(any('| Name | Value |\n|---|---|\n| A | B |' in c for c in chunks))

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.p1=patch.object(store,'DB_FILE',Path(self.tmp.name)/'research.sqlite3')
        self.p2=patch.object(local_config,'ENV_FILE',Path(self.tmp.name)/'.env')
        self.p1.start();self.p2.start()
        self.client=TestClient(server.app).__enter__()
        self.admin_cookie=self.client.cookies.get(accounts.COOKIE)
        self.admin_id=self.client.get('/api/auth/me').json()['id']

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.p2.stop();self.p1.stop();self.tmp.cleanup()

    def register(self, name):
        response=self.client.post('/api/auth/register',json={'username':name,'password':'a-long-password-123','role':'admin','max_calls':99999})
        self.assertEqual(response.status_code,201,response.text)
        self.assertNotIn(accounts.COOKIE,response.cookies)
        self.client.cookies.clear()
        login=self.client.post('/api/auth/login',json={'username':name,'password':'a-long-password-123'})
        self.assertEqual(login.status_code,200,login.text)
        cookie=login.cookies.get(accounts.COOKIE)
        header={'Cookie':accounts.COOKIE+'='+cookie}
        user=self.client.get('/api/auth/me',headers=header).json()
        return header,user

    def configure(self,header,name):
        data={'base_url':f'https://{name}.mock/v1','model':f'model-{name}','api_key':f'key-{name}','search_key':f'search-{name}'}
        self.assertEqual(self.client.put('/api/settings',headers=header,json=data).status_code,200)
        return data

    def test_registration_is_member_and_configuration_is_personal(self):
        local_config.save_settings({'api_key':'admin-private-key','search_key':'admin-private-search'})
        header,u=self.register('first')
        self.assertEqual(u['role'],'member');self.assertEqual(u['max_calls'],400)
        self.assertFalse(self.client.get('/api/settings',headers=header).json()['ready'])
        self.assertEqual(self.client.post('/api/runs',headers=header,json={'question':'not configured'}).status_code,400)
        config=self.configure(header,'first')
        second,v=self.register('second')
        self.assertFalse(self.client.get('/api/settings',headers=second).json()['ready'])
        self.configure(second,'second')
        self.assertEqual(self.client.get('/api/settings',headers=header).json()['model'],'model-first')
        self.assertNotIn(config['api_key'],self.client.get('/api/settings',headers=header).text)
        captured=[]
        async def checks(current):
            captured.append(current)
            return [{'ok':True,'message':'ok'}]
        with patch('connection_checks.test_connections',checks):
            self.assertEqual(self.client.post('/api/settings/test',headers=header).status_code,200)
            self.assertEqual(self.client.post('/api/settings/test',headers=second).status_code,200)
        self.assertEqual([x['api_key'] for x in captured],['key-first','key-second'])
        admin={'Cookie':accounts.COOKIE+'='+self.admin_cookie}
        self.assertEqual(self.client.get('/api/settings',headers=admin).json()['model'],'deepseek-chat')
        self.assertEqual(self.client.post('/api/auth/register',json={'username':'first','password':'a-long-password-123'}).status_code,400)
        self.assertEqual(self.client.get('/register').status_code,200)
        self.assertEqual(self.client.get('/api/admin/users',headers=header).status_code,403)

    def test_capacity_limit_and_own_settings_lock(self):
        users=[]
        for name in ('first','second','third','fourth','fifth'):
            header,_=self.register(name);self.configure(header,name);users.append(header)
        release=threading.Event()
        async def plan(rid,*args):
            try:
                while not release.is_set():await asyncio.sleep(.01)
                store.finish_phase(rid,'awaiting_confirmation')
            except asyncio.CancelledError:
                store.finish_phase(rid,'cancelled');raise
        with patch.object(runtime,'generate_plan',plan):
            try:
                for i,header in enumerate(users[:4]):
                    result=self.client.post('/api/runs',headers=header,json={'question':'question '+str(i)})
                    self.assertEqual(result.status_code,201,result.text)
                self.assertEqual(self.client.post('/api/runs',headers=users[4],json={'question':'capacity'}).status_code,503)
                self.assertEqual(self.client.put('/api/settings',headers=users[0],json={'base_url':'https://changed.mock','model':'changed'}).status_code,409)
                # Other users can configure their own keys while four jobs run.
                self.configure(users[4],'fifth')
            finally:release.set()

    def test_two_users_can_run_at_once_and_cancel_independently(self):
        one,u=self.register('first');self.configure(one,'first')
        two,v=self.register('second');self.configure(two,'second')
        release=threading.Event();received={}
        async def plan(rid,question,current,depth):
            received[question]=current
            try:
                while not release.is_set():await asyncio.sleep(.01)
                store.finish_phase(rid,'awaiting_confirmation')
            except asyncio.CancelledError:
                store.finish_phase(rid,'cancelled');raise
        with patch.object(runtime,'generate_plan',plan):
            a=self.client.post('/api/runs',headers=one,json={'question':'first question'})
            b=self.client.post('/api/runs',headers=two,json={'question':'second question'})
            self.assertEqual(a.status_code,201,a.text);self.assertEqual(b.status_code,201,b.text)
            self.assertEqual(self.client.post('/api/runs',headers=one,json={'question':'duplicate'}).status_code,409)
            arid=a.json()['id'];brid=b.json()['id']
            self.assertEqual(store.get(arid)['report_language'],'zh')
            self.assertEqual(store.get(brid)['status'],'planning')
            self.assertEqual(self.client.post('/api/runs/'+arid+'/cancel',headers=one).status_code,200)
            self.assertEqual(store.get(arid)['status'],'cancelled')
            self.assertEqual(store.get(brid)['status'],'planning')
            self.assertEqual(received['first question']['api_key'],'key-first')
            self.assertEqual(received['second question']['api_key'],'key-second')
            self.assertEqual(self.client.get('/api/runs/'+brid,headers=one).status_code,404)
            release.set()
            for _ in range(100):
                if store.get(brid)['status']=='awaiting_confirmation':break
                time.sleep(.01)
            self.assertEqual(store.get(brid)['status'],'awaiting_confirmation')

    def test_official_graph_concurrent_credentials_sources_and_languages(self):
        owners=[]
        for name in ('first','second'):
            header,u=self.register(name);owners.append(u)
        records=[];calls=[]
        for i,name in enumerate(('first','second')):
            token=accounts.CURRENT_USER.set(accounts.user(owners[i]['id']))
            try:
                run=store.create('question-'+name,status='running',report_language='zh' if i==0 else 'en')
                store.update(run['id'],plan={'objective':'goal-'+name,'outline':['Evidence'],'focus':'evidence','exclusions':'irrelevant','source_preferences':'primary'})
                records.append(run)
            finally:accounts.CURRENT_USER.reset(token)
        async def handle(request):
            await asyncio.sleep(.005)
            name=request.url.host.split('.')[0]
            self.assertEqual(request.headers['authorization'],'Bearer key-'+name)
            payload=json.loads(request.content)
            self.assertEqual(payload['model'],'model-'+name)
            self.assertNotIn('goal-'+('second' if name=='first' else 'first'),str(payload['messages']))
            names=[t['function']['name'] for t in payload.get('tools',[])]
            calls.append(name)
            tool,args,content=None,None,None
            if 'ConductResearch' in names:
                done=any(m['role']=='tool' for m in payload['messages'])
                tool='ResearchComplete' if done else 'ConductResearch';args={} if done else {'research_topic':'look up evidence'}
            elif 'Summary' in names:tool,args='Summary',{'summary':'summary '+name,'key_excerpts':'excerpt '+name}
            elif 'tavily_search' in names:
                done=any(m['role']=='tool' for m in payload['messages'])
                tool='ResearchComplete' if done else 'tavily_search';args={} if done else {'queries':['evidence']}
            else:
                if '<Research Brief>' in str(payload['messages']):
                    self.assertEqual(payload['messages'][0]['role'],'system')
                    self.assertIn('简体中文' if name=='first' else 'English',payload['messages'][0]['content'])
                content='# '+('中文研究报告' if name=='first' else 'English report')+f'\n[Evidence](https://{name}.example/evidence)'
            message={'role':'assistant','content':content}
            if tool:message['tool_calls']=[{'id':'call_'+str(len(calls)),'type':'function','function':{'name':tool,'arguments':json.dumps(args)}}]
            return httpx.Response(200,json={'id':'mock','object':'chat.completion','created':1,'model':'model-'+name,'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if tool else 'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})
        real=runtime.CompatibleChatModel
        class Model(real):
            def __init__(self,**kwargs):
                kwargs['http_client']=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
                kwargs['http_async_client']=httpx.AsyncClient(transport=httpx.MockTransport(handle))
                super().__init__(**kwargs)
        class Search:
            def __init__(self,api_key):self.name=api_key.removeprefix('search-')
            async def search(self,query,**kwargs):
                await asyncio.sleep(.01)
                return {'query':query,'results':[{'title':'Evidence '+self.name,'url':f'https://{self.name}.example/evidence','content':'facts '+self.name,'raw_content':'private excerpt '+self.name}]}
        async def execute():
            await asyncio.gather(*(runtime.run_research(r['id'],r['question'],{'base_url':f'https://{name}.mock/v1','model':'model-'+name,'api_key':'key-'+name,'search_key':'search-'+name},'quick') for r,name in zip(records,('first','second'))))
        with patch.object(runtime,'CompatibleChatModel',Model),patch.object(runtime.utils,'AsyncTavilyClient',Search):asyncio.run(execute())
        for r,name in zip(records,('first','second')):
            result=store.get(r['id'])
            self.assertEqual(result['status'],'complete',result['error'])
            self.assertEqual(result['sources'][0]['url'],f'https://{name}.example/evidence')
            self.assertEqual(len(result['sources']),1)
            self.assertGreater(result['usage']['model_calls'],3)
        self.assertIn('中文研究报告',store.get(records[0]['id'])['report'])
        self.assertIn('English report',store.get(records[1]['id'])['report'])
        self.assertIsNone(runtime.RUNTIME_CONTEXT.get())

    def test_translation_preserves_original_cache_exports_and_quota(self):
        run=store.create('translation',status='complete',report_language='en')
        source='# Original report\n[Evidence](https://example.com/evidence)'
        store.save_report(run['id'],source,'original')
        local_config.save_settings({'api_key':'test-key','search_key':'search-key'})
        responses=['# 中文报告\n[证据](https://example.com/evidence)']
        def handle(request):
            payload=json.loads(request.content)
            self.assertIn('简体中文',payload['messages'][0]['content'])
            return httpx.Response(200,json={'id':'mock','object':'chat.completion','created':1,'model':'test','choices':[{'index':0,'message':{'role':'assistant','content':responses[-1]},'finish_reason':'stop'}],'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}})
        real=runtime.CompatibleChatModel
        class Model(real):
            def __init__(self,**kwargs):
                kwargs['http_client']=httpx.Client(transport=httpx.MockTransport(handle));kwargs['http_async_client']=httpx.AsyncClient(transport=httpx.MockTransport(handle));super().__init__(**kwargs)
        path='/api/runs/'+run['id']
        with patch.object(runtime,'CompatibleChatModel',Model):
            result=self.client.post(path+'/translate',json={'language':'zh'})
            self.assertEqual(result.status_code,200,result.text)
            for _ in range(100):
                item=self.client.get(path).json()
                if item['status']!='running':break
                time.sleep(.01)
            self.assertEqual(item['status'],'complete',item['error'])
            self.assertEqual(item['report'],source)
            self.assertEqual(item['translations'][0]['language'],'zh')
            self.assertIn('中文报告',self.client.get(path+'/report?language=zh').text)
            self.assertEqual(self.client.get(path+'/export/docx?language=zh').status_code,200)
            calls=item['usage']['model_calls']
            self.client.post(path+'/translate',json={'language':'zh'})
            self.assertEqual(store.get(run['id'])['usage']['model_calls'],calls)
            store.save_report(run['id'],source+'\nchanged','revision')
            self.assertEqual(self.client.get(path).json()['translations'],[])
            self.assertEqual(self.client.get(path+'/report?language=zh').status_code,409)
            responses.append('# 中文报告但缺少链接')
            self.client.post(path+'/translate',json={'language':'zh'})
            for _ in range(100):
                item=self.client.get(path).json()
                if item['status']!='running':break
                time.sleep(.01)
            self.assertEqual(item['status'],'failed')
            self.assertIn('来源链接',item['error'])
            self.assertEqual(item['translations'],[])
            self.assertEqual(item['report'],source+'\nchanged')


if __name__=='__main__':unittest.main()
