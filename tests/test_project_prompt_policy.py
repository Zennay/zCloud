import server


ACTIVE_PROJECTS = ("haxlab", "ftmo", "cloud", "supa", "raiseai", "ulab", "zssh")


def worker_prompt(project_id="cloud"):
    project = server.PROJECT_INDEX[project_id]
    base = server.project_runner_prompt(project_id, project["name"])
    return server.project_worker_prompt(project_id, project["name"], base, 1, 2)


def test_all_active_workers_have_canonical_notion_sources():
    for project_id in ACTIVE_PROJECTS:
        project = server.PROJECT_INDEX[project_id]
        assert project.get("notion_url"), project_id
        assert project.get("handoff_url"), project_id


def test_worker_prompts_are_global_queue_first_compact_and_execution_first():
    for project_id in ACTIVE_PROJECTS:
        prompt = worker_prompt(project_id)
        assert "Begin ELKE cyclus met een verse Notion Portfolio Work Queue-check" in prompt
        assert "queue-RIJEN via de Notion data-source query" in prompt
        assert "schema/search/fetch alleen telt niet als queue-check" in prompt
        assert "VOER UIT, NIET RAPPORTEREN" in prompt
        assert "alleen leest, controleert of status samenvat is ongeldig" in prompt
        assert "eerste toolroute is NOOIT op zichzelf een WAIT/blocker" in prompt
        assert "IN DEZELFDE CYCLUS het volgende eligible queue-item" in prompt
        assert "geen tweede statusbericht" in prompt
        assert "Done alleen wanneer alle Completion Criteria bewezen zijn" in prompt
        assert "WAIT is alleen toegestaan wanneer een VERSE queue-query bevestigt" in prompt
        assert "ZCLOUD_WORK_PROJECT:" in prompt
        assert "ZCLOUD_AUTONOMY:" in prompt
        assert len(prompt) < 3600


def test_scenario_normal_queue_means_claim_then_execute_not_status_only():
    prompt = worker_prompt()
    claim = prompt.index("claim je het hoogste Eligible+Queued item")
    execute = prompt.index("EXECUTION-FIRST")
    output = prompt.index("OUTPUT:")
    assert claim < execute < output
    assert "Voor je antwoord moet er minimaal één echte write, run/job, geverifieerde evidence of materiële state-change zijn." in prompt


def test_scenario_stale_or_repeated_item_cannot_loop_on_status():
    prompt = worker_prompt()
    assert "verifieer Worker + lease server-side" in prompt
    assert "Kies je hetzelfde item opnieuw zonder nieuwe evidence/state-change" in prompt
    assert "uitvoeren of block/releasen" in prompt


def test_scenario_tool_route_failure_must_fallback_before_wait():
    prompt = worker_prompt()
    fallback = prompt.index("eerste toolroute is NOOIT op zichzelf een WAIT/blocker")
    wait = prompt.index("WAIT is alleen toegestaan")
    assert fallback < wait
    assert "probeer direct een andere beschikbare veilige route/query" in prompt
    assert "queue=no-eligible" in prompt
