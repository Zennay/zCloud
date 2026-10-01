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


def test_worker_prompt_is_short_execution_first_and_queue_owned():
    item = {
        "queue_id": "cloud-test",
        "project_id": "cloud",
        "priority": "P0",
        "title": "Test VPS queue",
        "completion_criteria": "green evidence",
        "source_url": "https://example.invalid/source",
    }
    prompt = worker_prompt("cloud", item)
    assert "Ga door met de queue: de SQLite queue van zCloud op de VPS" in prompt
    assert "Notion is alleen documentatie, nooit scheduler of blocker" in prompt
    assert "VPS_QUEUE_ASSIGNMENT id=cloud-test" in prompt
    assert "Stop niet na één actie" in prompt
    assert "Een status- of auditrapport is geen resultaat" in prompt
    assert "probeer meteen een andere veilige route" in prompt
    assert "Pas helemaal aan het einde" in prompt
    assert "DONE alleen met bewijs, anders CONTINUE" in prompt
    assert "ZCLOUD_QUEUE_ITEM: cloud-test" in prompt
    assert "Worker 1/2." in prompt
    assert "ZCLOUD_QUEUE_RESULT: DONE|CONTINUE" in prompt
    assert "ZCLOUD_AUTONOMY: CONTINUE|WAIT_HUMAN|COMPLETE" in prompt
    assert "TOEGANG & ROUTE" not in prompt
    assert "Zeg NOOIT geen toegang" not in prompt
    assert len(prompt) < 2300  # owner-requested intro + end-of-run next-task block (was 1800)


def test_stale_persisted_base_prompt_is_ignored():
    stale = "STALE HUGE PROMPT TOEGANG & ROUTE Zeg NOOIT geen toegang BLOCKED WAIT_VPS"
    prompt = worker_prompt(
        "cloud",
        {
            "queue_id": "cloud-test",
            "project_id": "cloud",
            "priority": "P1",
            "title": "Do work",
            "completion_criteria": "green",
            "source_url": "",
        },
        base_prompt=stale,
    )
    assert stale not in prompt
    assert "TOEGANG & ROUTE" not in prompt
    assert "WAIT_VPS" in prompt  # only the concise shared VPS rule remains


def test_no_assignment_stays_local_and_simple():
    prompt = worker_prompt()
    assert "VPS_QUEUE_ASSIGNMENT none" in prompt
    assert "Notion is alleen documentatie, nooit scheduler of blocker" in prompt
    assert len(prompt) < 2300  # owner-requested intro + end-of-run next-task block; longest case (cloud guard) is 2208


def test_project_specific_guard_is_preserved():
    prompt = worker_prompt("ftmo", {
        "queue_id": "ftmo-test",
        "project_id": "ftmo",
        "priority": "P1",
        "title": "Validate",
        "completion_criteria": "green",
        "source_url": "",
    })
    assert "preregistration, walk-forward en final holdout gescheiden" in prompt
