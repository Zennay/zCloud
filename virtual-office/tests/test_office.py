"""Contract tests for the zCloud ChatGPT-chat bridge: entirely offline and isolated."""
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class OfficeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(server, "DB", Path(self.tmp.name) / "office.db")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        server.init_db()

    def test_persistent_outbox_without_browser(self):
        job = server.enqueue("maya", "task", "Bouw een dashboardtest")
        self.assertEqual(job["status"], "queued")
        server.init_db()
        self.assertEqual(server.get_job(job["id"])["instruction"], "Bouw een dashboardtest")
        self.assertEqual(server.office_queue_id(job), server.office_queue_id(job))
        self.assertEqual(len(server.list_jobs()), 1)

    def test_no_fake_freeform_chat_or_codex(self):
        with self.assertRaisesRegex(ValueError, "Vrije gesprekken"):
            server.enqueue("noah", "chat", "Hoi")
        with self.assertRaises(ValueError):
            server.enqueue("nobody", "task", "Doe QA")
        with self.assertRaises(ValueError):
            server.enqueue("maya", "task", " ")
        self.assertFalse(hasattr(server, "available_codex"))
        self.assertFalse(hasattr(server, "execute"))

    def test_dispatch_to_actual_project_queue(self):
        job = server.enqueue("lina", "task", "Repareer de LightUp-tests")
        posted = []
        def fake_zcloud(path, payload=None, timeout=3.5):
            posted.append((path, payload))
            return {"ok": True, "item": {"queue_id": payload["queue_id"], "status": "queued"}}
        with patch.object(server, "zcloud", side_effect=fake_zcloud):
            self.assertTrue(server.dispatch_once())
            self.assertFalse(server.dispatch_once())
        result = server.get_job(job["id"])
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(result["remote_status"], "queued")
        self.assertEqual(posted[0][1]["project_id"], "lightup")
        self.assertEqual(posted[0][1]["priority"], "P3")
        self.assertEqual(posted[0][1]["queue_id"], server.office_queue_id(job))
        self.assertIn("Repareer de LightUp-tests", posted[0][1]["completion_criteria"])

    def test_zcloud_unavailable_keeps_outbox(self):
        job = server.enqueue("noah", "task", "Bouw de Supa-kaart")
        with patch.object(server, "zcloud", side_effect=ConnectionError("offline")):
            self.assertFalse(server.dispatch_once())
        self.assertEqual(server.get_job(job["id"])["status"], "queued")
        self.assertIn("offline", server.get_job(job["id"])["error"])
        with patch.object(server, "zcloud", return_value={"item": {"queue_id":server.office_queue_id(job),"status":"queued"}}):
            self.assertTrue(server.dispatch_once())
        self.assertEqual(server.get_job(job["id"])["status"], "dispatched")

    def test_reconcile_from_zcloud_queue_not_from_fake_reply(self):
        job = server.enqueue("maya", "task", "Maak concrete codefix")
        remote_id = server.office_queue_id(job)
        with patch.object(server, "zcloud", return_value={"item":{"queue_id":remote_id,"status":"queued"}}):
            server.dispatch_once()
        with patch.object(server, "zcloud", return_value={"items":[{"queue_id":remote_id,"status":"running"}]}):
            server.sync_remote()
        self.assertEqual(server.get_job(job["id"])["status"], "running")
        with patch.object(server, "zcloud", return_value={"items":[{"queue_id":remote_id,"status":"done"}]}):
            server.sync_remote(); server.sync_remote()
        self.assertEqual(server.get_job(job["id"])["status"], "done")
        self.assertNotIn("code",server.get_job(job["id"])["output"])
        with server.connect() as db:
            counts = db.execute("SELECT COUNT(*) FROM messages WHERE role='system'").fetchone()[0]
        self.assertEqual(counts, 2)  # durable acknowledgement + terminal queue result

    def test_chitchat_cannot_be_dispatched_into_write_queue(self):
        job = server.enqueue("maya","task","Wat ga je vandaag doen?")
        with patch.object(server, "zcloud") as z:
            self.assertTrue(server.dispatch_once())
            z.assert_not_called()
        self.assertEqual(server.get_job(job["id"])["status"],"failed")

    def test_retry_only_unsent(self):
        job = server.enqueue("noah","task","Hoi, hoe gaat het?")
        server.dispatch_once()
        self.assertEqual(server.get_job(job["id"])["status"],"failed")
        self.assertEqual(server.retry(job["id"])["status"],"queued")
        self.assertEqual(server.get_job(job["id"])["status"], "queued")
        self.assertIsNone(server.retry(job["id"]))

    def test_project_chat_links_from_live_targets(self):
        targets={"cloud::w1":{"base_project_id":"cloud","provider":"chatgpt","conversation_id":"01234567-89ab-cdef-0123-456789abcdef"},
                 "supa::w1":{"base_project_id":"supa","provider":"claude","conversation_id":"deadbeef"},
                 "lightup::w1":{"base_project_id":"lightup","provider":"chatgpt","conversation_id":"bad.example/x"}}
        with patch.object(server,"zcloud",return_value={"projects":targets}):
            links=server.employee_chat_links()
        self.assertEqual(links["maya"],"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef")
        self.assertEqual(links["noah"],"https://chatgpt.com/")
        self.assertEqual(links["lina"],"https://chatgpt.com/")

    def test_bounded_concurrency(self):
        job=server.enqueue("noah","task","Repareer Supa-test")
        barrier=threading.Barrier(3)
        def actor(_):
            barrier.wait()
            with patch.object(server,"zcloud",return_value={"item":{"queue_id":server.office_queue_id(job)}}):
                return server.dispatch_once()
        with ThreadPoolExecutor(max_workers=3) as pool:
            accepted=list(pool.map(actor,range(3)))
        self.assertEqual(sum(accepted),1)
        self.assertEqual(server.get_job(job["id"])["status"],"dispatched")


class HTTPTests(OfficeTests):
    def setUp(self):
        super().setUp()
        self.auth_patch=patch.object(server,"TOKEN","test-only-password")
        self.health_patch=patch.object(server,"zcloud_health",return_value={"zcloud_online":False,"chatgpt_worker_data":False,"firefox":False})
        self.auth_patch.start();self.health_patch.start()
        self.addCleanup(self.auth_patch.stop);self.addCleanup(self.health_patch.stop)
        self.http=ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        self.addCleanup(self.http.server_close)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(lambda:(self.http.shutdown(),self.thread.join(timeout=3)))

    def ask(self,path,body=None,token=None,content_type="application/json"):
        headers={}
        if token:headers["X-Office-Token"]=token
        if body is not None:headers["Content-Type"]=content_type
        request=Request("http://127.0.0.1:"+str(self.http.server_address[1])+path,
                         data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urlopen(request,timeout=3) as response:
                return response.status,json.load(response)
        except HTTPError as error:
            return error.code,json.load(error)

    def test_access_control_and_http(self):
        code,health=self.ask("/api/health")
        self.assertEqual(code,200)
        self.assertFalse(health["zcloud_online"])
        self.assertEqual(health["mode"],"zcloud_chatgpt_chats")
        self.assertEqual(self.ask("/api/jobs")[0],401)
        self.assertEqual(self.ask("/api/jobs",{"employee":"maya","kind":"task","instruction":"Bouw UI"})[0],401)
        self.assertEqual(self.ask("/api/jobs",{"employee":"maya","kind":"task","instruction":"Bouw UI"},token="wrong")[0],401)
        # A pasted non-ASCII Office code must yield a JSON 401, not abruptly close
        # the socket and surface "Failed to fetch" in the browser.
        self.assertEqual(self.ask("/api/jobs", token="wrong-é")[0], 401)
        self.assertEqual(self.ask("/api/relay/status", token="ongeldig-é")[0], 401)
        self.assertEqual(self.ask("/api/jobs", token="x" * 513)[0], 401)
        self.assertEqual(self.ask("/api/jobs",{"employee":"maya","kind":"task","instruction":"Bouw UI"},token="test-only-password",content_type="text/plain")[0],415)
        code,job=self.ask("/api/jobs",{"employee":"maya","kind":"task","instruction":"Bouw UI"},token="test-only-password")
        self.assertEqual(code,201)
        self.assertEqual(self.ask("/api/jobs",token="test-only-password")[1]["jobs"][0]["id"],job["id"])

if __name__=="__main__":
    unittest.main()
