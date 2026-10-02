import server


ACTIVE_PROJECTS = ("haxlab", "ftmo", "cloud", "supa", "raiseai", "ulab", "zssh")


def worker_prompt(project_id="cloud", queue_item=None, base_prompt=None):
    project = server.PROJECT_INDEX[project_id]
    base = base_prompt if base_prompt is not None else server.project_runner_prompt(project_id, project["name"])
    return server.project_worker_prompt(project_id, project["name"], base, 1, 2, queue_item)


def test_all_active_workers_keep_canonical_documentation_sources():
    for project_id in ACTIVE_PROJECTS:
        project = server.PROJECT_INDEX[project_id]
        assert project.get("notion_url"), project_id
        assert project.get("handoff_url"), project_id


def test_worker_prompt_is_short_project_first_and_not_queue_owned():
    item = {
        "queue_id": "cloud-test",
        "project_id": "cloud",
        "priority": "P0",
        "title": "Internal queue coordination",
        "completion_criteria": "internal only",
        "source_url": "",
        "execution_lane": {
            "lane_id": "control-plane",
            "scope": {"capabilities": ["zcloud-control-plane"], "files": []},
        },
    }
    prompt = worker_prompt("cloud", item)
    assert prompt.startswith("Werk verder aan zCloud.")
    assert "Kijk in Notion in welke fase het project zit" in prompt
    assert "Werkgebied:" not in prompt
    assert "claims" not in prompt
    assert "VPS_QUEUE_ASSIGNMENT" not in prompt
    assert "ZCLOUD_QUEUE_" not in prompt
    assert "Jij bent Worker" not in prompt
    assert "Ga door met de queue" not in prompt
    assert len(prompt) < 500


def test_stale_persisted_base_prompt_is_ignored():
    stale = "STALE HUGE PROMPT TOEGANG & ROUTE Zeg NOOIT geen toegang BLOCKED WAIT_VPS"
    prompt = worker_prompt("cloud", None, base_prompt=stale)
    assert stale not in prompt
    assert "TOEGANG & ROUTE" not in prompt
    assert "WAIT_VPS" not in prompt
    assert prompt.startswith("Werk verder aan zCloud.")


def test_no_assignment_stays_local_and_simple():
    prompt = worker_prompt()
    assert prompt.startswith("Werk verder aan zCloud.")
    assert "kies een vrij onderdeel" in prompt
    assert "VPS_QUEUE_ASSIGNMENT" not in prompt
    assert len(prompt) < 400


def test_collision_coordination_is_not_exposed_as_prompt_bloat():
    prompt = worker_prompt("ftmo")
    assert prompt == server.project_runner_prompt("ftmo", "FTMO")
    assert "Werkgebied:" not in prompt
    assert "claims" not in prompt
    assert "preregistration, walk-forward en final holdout" not in prompt
