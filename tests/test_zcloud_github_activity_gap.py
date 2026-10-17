import datetime as dt
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'zcloud_github_activity_gap.py'
spec = importlib.util.spec_from_file_location('gap', SCRIPT)
gap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gap)


class GapAuditTests(unittest.TestCase):
    def test_repo_whitelist_rejects_untrusted_urls(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'projects.json'
            p.write_text(json.dumps([{'id':'supa','repo_url':'https://github.com/Zennay/Supa'},
                                     {'id':'foreign','repo_url':'https://evil.test/Zennay/private'},
                                     {'id':'cloud','repo_url':'https://github.com/Zennay/zCloud'}]))
            self.assertEqual(gap.project_repos(p), {'supa':'Supa','cloud':'zCloud'})

    def test_missing_registry_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                gap.project_repos(Path(d) / 'missing')

    def test_invalid_timestamp_not_silent(self):
        for invalid in ['2026-10-17', '2026-13-99T00:00:00Z', '', None, 5]:
            self.assertIsNone(gap.timestamp(invalid))

    def test_creation_not_delivery_and_duplicate_page(self):
        since = dt.datetime(2026,10,17,0,0,tzinfo=dt.timezone.utc)
        a={'number':10,'created_at':'2026-10-17T01:00:00Z','updated_at':'2026-10-17T03:00:00Z','merged_at':None}
        b={'number':11,'created_at':'2026-10-16T00:00:00Z','updated_at':'2026-10-17T04:00:00Z','merged_at':'2026-10-17T04:00:00Z'}
        c=gap.pr_summary([[a,b],[a]],since)
        self.assertEqual((c['created'],c['merged'],c['updated']), (1,1,2))
        self.assertEqual(c['invalid_records'],1)

    def test_oversized_or_invalid_remote_rejected(self):
        class Provider:
            def __init__(self,data): self.data=data
            def open(self,request,timeout):
                self.request=request
                response=Mock()
                response.status=200
                response.read.return_value=self.data
                response.__enter__=lambda s:s
                response.__exit__=lambda *args:None
                return response
        good=Provider(b'[]')
        self.assertEqual(gap.api_pr_page('Supa',1,opener=good),[])
        self.assertEqual(good.request.full_url.split('?')[0],'https://api.github.com/repos/Zennay/Supa/pulls')
        for payload in [b'not-json',b'{"message":"Bad credentials"}',b'x'*(gap.MAX_BYTES+1), b'\xff']:
            with self.assertRaises(ValueError):
                gap.api_pr_page('Supa',1,opener=Provider(payload))
        with self.assertRaises(ValueError):
            gap.api_pr_page('Supa/../../haxlab',1,opener=good)

    def test_readonly_db_keeps_bytes_and_detects_missing_project(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'history.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE events (id TEXT PRIMARY KEY, ts TEXT, project TEXT, kind TEXT, title TEXT, detail TEXT)')
                for i in range(90):
                    db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',(str(i),'2026-10-17T00:30:00+00:00','supa','commit','secret','secret'))
                db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('other','2026-10-17T00:45:00+00:00','mixlab','commit','secret','secret'))
            before=path.read_bytes()
            ages=gap.read_latest_local(path,['supa','mixlab','lightup'])
            self.assertEqual(ages['mixlab'].isoformat(),'2026-10-17T00:45:00+00:00')
            self.assertIsNone(ages['lightup'])
            self.assertEqual(before,path.read_bytes())
            symlink=Path(d)/'alias.db'
            symlink.symlink_to(path)
            with self.assertRaises(ValueError): gap.read_latest_local(symlink,['supa'])

    def test_future_merged_at_accepted_only_if_after_created(self):
        since=dt.datetime(2026,10,17,tzinfo=dt.timezone.utc)
        bad={'number':20,'created_at':'2026-10-17T04:00:00Z','updated_at':'2026-10-17T04:00:00Z', 'merged_at':'2026-10-17T03:00:00Z'}
        result=gap.pr_summary([[bad]],since)
        self.assertEqual(result['merged'],0)
        self.assertEqual(result['invalid_records'],1)


if __name__=='__main__': unittest.main()
