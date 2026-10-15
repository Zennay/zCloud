#!/usr/bin/env python3
"""Developer/test tool: a tiny stateful stand-in for the zCloud loopback API (workers, priorities, queue).

  python3 tools/fake_zcloud.py PORT

It answers the same endpoints the Office control room uses, and keeps state like the real thing, so a change made
through Office (or POST /__dashboard/pause?worker=ID, which imitates the dashboard) is visible to every reader.
It is NOT the real zCloud and only exists so the UI can be exercised end to end without a VPS.
"""
import json
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

STATE = {
    "projects": {"haxlab-1": {"base_project_id": "haxlab", "active": True, "desired_state": "running", "provider": "chatgpt", "worker_slot": 1,
                              "queue_item": {"queue_id": "haxlab-aaa", "priority": "P2"}},
                "ftmo-1": {"base_project_id": "ftmo", "active": True, "desired_state": "running", "provider": "chatgpt", "worker_slot": 1}},
    "max_workers": 4, "priority": {"haxlab": "normal", "ftmo": "high", "cloud": "normal"},
    "queue": [{"queue_id": "haxlab-aaa", "project_id": "haxlab", "title": "Bouw de arena", "priority": "P2", "status": "claimed"},
              {"queue_id": "ftmo-bbb", "project_id": "ftmo", "title": "Valideer de strategie", "priority": "P1", "status": "queued"}],
    "log": [],
}
NAMES = {"haxlab": "Haxlab", "ftmo": "FTMO", "cloud": "zCloud"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, doc, code=200):
        body = json.dumps(doc).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/runner-targets":
            self.reply({"projects": STATE["projects"], "max_workers": STATE["max_workers"]})
        elif path == "/api/status":
            self.reply({"projects": [{"id": k, "name": NAMES[k], "resource": {"priority": v}} for k, v in STATE["priority"].items()]})
        elif path.startswith("/api/portfolio-queue"):
            self.reply({"items": STATE["queue"]})
        elif path == "/api/runner-live":
            self.reply({"chatgpt_runners": {"x": 1}, "chatgpt_firefox": {"active": True}})
        elif path == "/__state":
            self.reply(STATE)
        else:
            self.reply({})

    def do_POST(self):
        url = urlsplit(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        actor = self.headers.get("X-ZCloud-Actor", "")
        path = url.path
        STATE["log"].append([path, body, actor])
        if path == "/api/runner-control":
            worker = STATE["projects"].get(body.get("project_id"))
            if not worker:
                return self.reply({"error": "onbekende worker"}, 404)
            op = body.get("action")
            if op == "pause": worker["desired_state"] = "paused"
            if op == "start": worker["desired_state"] = "running"
            if op == "drain": worker["desired_state"] = "draining"
            return self.reply({"ok": True, "command_id": 1})
        if path == "/api/resource-priority":
            STATE["priority"][body["project"]] = body["priority"]
        elif path == "/api/runner-workers":
            pass
        elif path == "/api/portfolio-queue":
            if body.get("action") == "enqueue":
                STATE["queue"].append({"queue_id": body.get("queue_id") or uuid.uuid4().hex[:8], "project_id": body["project_id"], "title": body["title"], "priority": body.get("priority", "P2"), "status": "queued"})
            elif body.get("action") == "drop":
                STATE["queue"] = [q for q in STATE["queue"] if q["queue_id"] != body.get("queue_id")]
        elif path == "/__dashboard/pause":
            STATE["projects"][parse_qs(url.query)["worker"][0]]["desired_state"] = "paused"
        return self.reply({"ok": True})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
