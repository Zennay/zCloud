import json
from pathlib import Path

import server


def _fresh_queue(tmp_path):
    server.DB = tmp_path / "queue-test.db"
    server.PORTFOLIO_QUEUE_SEED_FILE = tmp_path / "seed.json"
    server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]", encoding="utf-8")
    server.init_db()


def test_vps_queue_claims_priority_and_two_global_slots(tmp_path):
    _fresh_queue(tmp_path)
    server.portfolio_queue_enqueue("haxlab", "normal", "P2", "prove normal")
    server.portfolio_queue_enqueue("raiseai", "high", "P1", "prove high")
    server.portfolio_queue_enqueue("cloud", "critical", "P0", "prove critical")

    selected = server.portfolio_queue_allocate()

    assert [item["priority"] for item in selected] == ["P0", "P1"]
    assert [item["worker_slot"] for item in selected] == [1, 2]
    allocation = server.global_worker_allocation()
    assert allocation["queue_backend"] == "sqlite"
    assert [worker["project_id"] for worker in allocation["workers"]] == ["cloud", "raiseai"]


def test_done_releases_slot_and_next_queue_item_is_claimed(tmp_path):
    _fresh_queue(tmp_path)
    first = server.portfolio_queue_enqueue("cloud", "first", "P0", "prove first")
    second = server.portfolio_queue_enqueue("haxlab", "second", "P1", "prove second")
    server.portfolio_queue_allocate()

    result = server.portfolio_queue_finish(1, first["queue_id"], "DONE", "commit abc; tests green")
    assert result["updated"] is True

    selected = server.portfolio_queue_allocate()
    current = server.portfolio_queue_current_for_slot(1)
    assert current["queue_id"] == second["queue_id"]
    assert current["status"] == "claimed"
    done = {item["queue_id"]: item for item in server.portfolio_queue_items(True)}[first["queue_id"]]
    assert done["status"] == "done"
    assert done["eligible"] is False


def test_continue_requeues_without_notion_dependency(tmp_path):
    _fresh_queue(tmp_path)
    item = server.portfolio_queue_enqueue("raiseai", "iterate", "P1", "prove next state")
    server.portfolio_queue_allocate()

    result = server.portfolio_queue_finish(1, item["queue_id"], "CONTINUE", "state advanced")
    assert result["updated"] is True
    queued = {row["queue_id"]: row for row in server.portfolio_queue_items(True)}[item["queue_id"]]
    assert queued["status"] == "queued"
    assert queued["eligible"] is True
    assert queued["worker_slot"] is None

    server.portfolio_queue_allocate()
    reclaimed = server.portfolio_queue_current_for_slot(1)
    assert reclaimed["queue_id"] == item["queue_id"]
