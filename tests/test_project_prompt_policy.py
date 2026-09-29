import server


ACTIVE_PROJECTS = ("haxlab", "ftmo", "cloud", "supa", "raiseai", "ulab", "zssh")


def worker_prompt(project_id="cloud", queue_item=None):
    project = server.PROJECT_INDEX[project_id]
    base = server.project_runner_prompt(project_id, project["name"])
    return server.project_worker_prompt(project_id, project["name"], base, 1, 2, queue_item)


def test_all_active_workers_keep_canonical_documentation_sources():
    for project_id in ACTIVE_PROJECTS:
        project = server.PROJECT_INDEX[project_id]
        assert project.get("notion_url"), project_id
        assert project.get("handoff_url"), project_id


def test_worker_prompt_uses_vps_queue_not_notion_for_scheduling():
    item = {
        "queue_id": "cloud-test",
        "project_id": "cloud",
        "priority": "P0",
        "title": "Test VPS queue",
        "completion_criteria": "green evidence",
        "source_url": "https://example.invalid/source",
    }
    prompt = worker_prompt("cloud", item)
    assert "zCloud SQLite op de VPS is de enige scheduling/source-of-truth" in prompt
    assert "VPS_QUEUE_ASSIGNMENT id=cloud-test" in prompt
    assert "Query Notion NIET om een queue-item te kiezen" in prompt
    assert "query_data_sources zijn nooit een WAIT/blocker" in prompt
    assert "VOER UIT, NIET RAPPORTEREN" in prompt
    assert "status-only/read-only cyclus is ongeldig" in prompt
    assert "RECOVERY-FIRST" in prompt
    assert "onderzoek de root cause" in prompt
    assert "bewijs de concrete blocker én de uitgevoerde herstelpogingen" in prompt
    assert "Done alleen wanneer ALLE Completion Criteria bewezen zijn" in prompt
    assert "ZCLOUD_QUEUE_ITEM: cloud-test" in prompt
    assert "Jij bent Worker 1/2." in prompt
    assert "{slot}" not in prompt
    assert "{total}" not in prompt
    assert "ZCLOUD_QUEUE_RESULT: DONE|BLOCKED|CONTINUE" in prompt
    assert "ZCLOUD_WORK_PROJECT:" in prompt
    assert "ZCLOUD_AUTONOMY:" in prompt
    assert len(prompt) < 3600


def test_no_assignment_never_invents_a_notion_queue_lookup():
    prompt = worker_prompt()
    assert "VPS_QUEUE_ASSIGNMENT none" in prompt
    assert "Begin ELKE cyclus met een verse Notion Portfolio Work Queue-check" not in prompt
    assert "queue-RIJEN via de Notion data-source query" not in prompt


def test_execution_first_and_followup_task_contract():
    prompt = worker_prompt("raiseai", {
        "queue_id": "raise-live",
        "project_id": "raiseai",
        "priority": "P1",
        "title": "Deploy live",
        "completion_criteria": "prove live response",
        "source_url": "",
    })
    execute = prompt.index("EXECUTION-FIRST")
    output = prompt.index("OUTPUT exact")
    assert execute < output
    assert "minimaal één echte write, run/job, geverifieerde evidence of materiële state-change" in prompt
    assert "ZCLOUD_NEXT_TASK:" in prompt
    assert "verzin geen WAIT" in prompt
