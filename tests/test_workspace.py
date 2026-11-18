import asyncio
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from auth_client import TestClient

import local_config
import research_store as store
import research_runtime as runtime
from research_documents import extract
import server


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = patch.object(store, 'DB_FILE', Path(self.temp.name) / 'runs.sqlite3')
        self.env = patch.object(local_config, 'ENV_FILE', Path(self.temp.name) / '.env')
        self.db.start()
        self.env.start()
        store.initialize()
        local_config.save_settings({'api_key':'test-api', 'search_key':'test-search'})

    def tearDown(self):
        self.env.stop()
        self.db.stop()
        self.temp.cleanup()

    def test_upload_scope_parsing_and_plan_context(self):
        received = []
        async def plan(run_id, *args):
            received.append(store.document_context(run_id))
            store.finish_phase(run_id, 'awaiting_confirmation')
        with patch.object(runtime, 'generate_plan', plan), TestClient(server.app) as client:
            for name, data in [('bad.exe',b'text'), ('huge.md',b'a'*12001), ('empty.pdf',b'not a pdf')]:
                self.assertEqual(client.post('/api/documents', files={'file':(name,data)}).status_code, 400)
            item = client.post('/api/documents', files={'file':('../../notes.md', '# 项目资料\n成本为每月一百元。'.encode())}).json()
            self.assertEqual(item['name'], 'notes.md')
            self.assertNotIn('text', item)
            response = client.post('/api/runs', json={'question':'分析上传的成本资料', 'document_ids':[item['id']]})
            self.assertEqual(response.status_code, 201)
            run_id = response.json()['id']
            for _ in range(50):
                if received: break
                time.sleep(.01)
            self.assertIn('每月一百元', received[0])
            run = client.get('/api/runs/'+run_id).json()
            self.assertEqual(run['sources'][0]['kind'],'document')
            self.assertIn('每月一百元', run['sources'][0]['excerpt'])
            self.assertIn('每月一百元', client.get('/api/documents/'+item['id']).text)
            self.assertEqual(client.post('/api/runs',json={'question':'不能复用绑定资料','document_ids':[item['id']]}).status_code,400)
            self.assertEqual(client.post('/api/runs/'+run_id+'/resume',json={}).status_code,409)

    def test_word_and_pdf_ingestion_and_exports(self):
        from docx import Document
        from pypdf import PdfReader
        from research_export import export
        d = Document()
        d.add_paragraph('Word 证据与中文测试')
        d.add_table(rows=1,cols=1).cell(0,0).text='关键数据 128'
        buf = BytesIO(); d.save(buf)
        self.assertIn('关键数据 128',extract('test.docx',buf.getvalue()))
        run = store.create('中文报告导出',status='complete')
        store.update(run['id'],report='# 中文报告\n\n结论与证据。\n\n## 成本\n\n| 项目 | 费用 |\n|---|---|\n| 模型 | 100 元 |\n\n## 限制\n\n需要进一步验证。',sources=[{'title':'中文来源','url':'https://example.com/a','retrieved_at':store.now()}])
        with TestClient(server.app) as client:
            pdf = client.get(f"/api/runs/{run['id']}/export/pdf")
            self.assertEqual(pdf.status_code,200)
            reader = PdfReader(BytesIO(pdf.content))
            text = '\n'.join(p.extract_text() for p in reader.pages)
            for word in ['中文报告','目录','参考来源','100 元','https://example.com/a']:
                self.assertIn(word,text)
            doc = client.get(f"/api/runs/{run['id']}/export/docx")
            self.assertEqual(doc.status_code,200)
            loaded = Document(BytesIO(doc.content))
            self.assertIn('100 元',loaded.tables[0].cell(1,1).text)
            self.assertTrue(any(p.text=='目录' for p in loaded.paragraphs))
            self.assertIn('中文报告',extract('export.pdf',pdf.content))

    def test_report_conversation_version_restore_and_accounting(self):
        prompts = []
        def handle(request):
            payload = json.loads(request.content)
            prompts.append(payload['messages'])
            return httpx.Response(200,json={'id':'mock','object':'chat.completion','created':1,'model':'test','choices':[{'index':0,'message':{'role':'assistant','content':'# 修改报告\n\n引用 [证据](https://example.com/evidence)。'},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}})
        real_model = runtime.CompatibleChatModel
        class Model(real_model):
            def __init__(self,**kwargs):
                kwargs['http_client']=httpx.Client(transport=httpx.MockTransport(handle))
                kwargs['http_async_client']=httpx.AsyncClient(transport=httpx.MockTransport(handle))
                super().__init__(**kwargs)
        run = store.create('报告版本测试',status='complete')
        store.update(run['id'],report='# 原始报告\n\n原始内容。',sources=[{'title':'证据','url':'https://example.com/evidence','excerpt':'已保存的独特证据'}],usage={'model_calls':267,'input_tokens':2000000,'output_tokens':200000})
        with patch.object(runtime,'CompatibleChatModel',Model), TestClient(server.app) as client:
            path = f"/api/runs/{run['id']}"
            result=client.post(path+'/followup',json={'prompt':'请补充成本说明','mode':'revise','budget':{'max_seconds':60,'max_model_calls':3,'max_tokens':10000}})
            self.assertEqual(result.status_code,200)
            for _ in range(100):
                item=client.get(path).json()
                if item['status']!='running':break
                time.sleep(.01)
            self.assertEqual(item['status'],'complete',item['error'])
            self.assertIn('已保存的独特证据',str(prompts))
            self.assertIn('原始内容',str(prompts))
            self.assertEqual(item['usage']['model_calls'],268)
            self.assertEqual(item['budget']['max_model_calls'],270)
            self.assertEqual(len(item['versions']),2)
            old = item['versions'][-1]['id']
            self.assertIn('原始内容',client.get(path+f'/versions/{old}').json()['report'])
            self.assertEqual(client.post(path+f'/versions/{old}/restore',json={}).status_code,200)
            self.assertIn('原始内容',client.get(path).json()['report'])
            self.assertEqual(client.post(path+'/resume',json={}).status_code,409)

    def test_pinning_order_search_and_restart_persistence(self):
        older = store.create('较早的技术研究', status='complete')
        newer = store.create('最新的技术研究', status='complete')
        with TestClient(server.app) as client:
            path = f"/api/runs/{older['id']}/pin"
            self.assertEqual(client.get('/api/runs').json()[0]['id'], newer['id'])
            self.assertEqual(client.patch(path, json={'pinned':True}).json()['pinned'], True)
            store.initialize()
            listed = client.get('/api/runs?q=技术').json()
            self.assertEqual([r['id'] for r in listed], [older['id'], newer['id']])
            self.assertTrue(listed[0]['pinned'])
            self.assertTrue(client.get(f"/api/runs/{older['id']}").json()['pinned'])
            client.patch(f"/api/runs/{older['id']}", json={'title':'收藏的研究','tags':['技术']})
            self.assertTrue(store.get(older['id'])['pinned'], 'Editing metadata must preserve pin state')
            self.assertEqual(client.patch(path, json={'pinned':'false'}).status_code, 422)
            self.assertEqual(client.patch(path, json={'pinned':False}, headers={'Origin':'https://evil.test'}).status_code,403)
            self.assertTrue(store.get(older['id'])['pinned'])
            self.assertEqual(client.patch(path, json={'pinned':False}).status_code,200)
            self.assertEqual(client.get('/api/runs').json()[0]['id'], newer['id'])
            self.assertFalse(store.get(older['id'])['pinned'])
            self.assertEqual(client.patch('/api/runs/missing/pin',json={'pinned':True}).status_code,404)

    def test_history_search_delete_and_partial_evidence(self):
        run=store.create('RAG 研究问题',status='cancelled')
        store.update(run['id'],sources=[{'title':'证据','url':'https://example.com','excerpt':'待核实的摘录'}],notes=['已完成的笔记'])
        doc=store.add_document('notes.txt','资料文字',12)
        store.attach_documents(run['id'],[doc['id']])
        store.save_report(run['id'],'# 保存版本','保存版本')
        store.update(run['id'],report='')
        with TestClient(server.app) as client:
            path=f"/api/runs/{run['id']}"
            self.assertEqual(client.patch(path,json={'title':'我的调研','tags':['工作','技术']}).status_code,200)
            self.assertEqual(len(client.get('/api/runs?q=工作').json()),1)
            self.assertEqual(len(client.get('/api/runs?q=RAG').json()),1)
            self.assertEqual(client.get('/api/runs?q=不存在').json(),[])
            partial=client.get(path).json()['partial_report_html']
            self.assertIn('尚未完成',partial)
            self.assertIn('待核实的摘录',partial)
            self.assertFalse(client.get(path).json()['can_resume'])
            self.assertEqual(client.delete(path,headers={'Origin':'https://evil.test'}).status_code,403)
            self.assertEqual(client.delete(path).status_code,200)
            self.assertEqual(client.get(path).status_code,404)
            self.assertEqual(client.get('/api/documents/'+doc['id']).status_code,404)
            self.assertIsNone(store.version(run['id'],1))


if __name__=='__main__':
    unittest.main()
