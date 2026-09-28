import server


ACTIVE_PROJECTS = ("haxlab", "ftmo", "cloud", "supa", "raiseai", "ulab", "zssh")


def test_all_active_workers_have_canonical_notion_sources():
    for project_id in ACTIVE_PROJECTS:
        project = server.PROJECT_INDEX[project_id]
        assert project.get("notion_url"), project_id
        assert project.get("handoff_url"), project_id


def test_worker_prompts_are_global_queue_first_compact_and_execution_first():
    prompts = []
    for project_id in ACTIVE_PROJECTS:
        project = server.PROJECT_INDEX[project_id]
        base = server.project_runner_prompt(project_id, project["name"])
        prompt = server.project_worker_prompt(project_id, project["name"], base, 1, 2)
        prompts.append(prompt)

        assert "Begin ELKE cyclus met een verse Notion Portfolio Work Queue-check" in prompt
        assert "runnerlabel" in prompt.lower()
        assert "VOER WERK UIT" in prompt
        assert "status-only/read-only cyclus is ongeldig" in prompt
        assert "CONTINUE is VERBODEN" in prompt
        assert "self-hosted GitHub Actions" in prompt
        assert "executeer of block/release" in prompt
        assert "Done alleen wanneer ALLE Completion Criteria bewezen zijn" in prompt
        assert "ZCLOUD_WORK_PROJECT:" in prompt
        assert "ZCLOUD_AUTONOMY:" in prompt
        assert len(prompt) < 5000
