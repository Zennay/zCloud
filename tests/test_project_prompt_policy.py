import server


ACTIVE_PROJECTS = ("haxlab", "ftmo", "cloud", "supa", "raiseai", "ulab", "zssh")


def test_all_active_workers_have_canonical_notion_sources():
    for project_id in ACTIVE_PROJECTS:
        project = server.PROJECT_INDEX[project_id]
        assert project.get("notion_url"), project_id
        assert project.get("handoff_url"), project_id


def test_worker_prompts_are_notion_first_and_execution_first():
    for project_id in ACTIVE_PROJECTS:
        project = server.PROJECT_INDEX[project_id]
        base = server.project_runner_prompt(project_id, project["name"])
        prompt = server.project_worker_prompt(project_id, project["name"], base, 1, 1)

        assert "Notion-first is verplicht" in prompt
        assert project["notion_url"] in prompt
        assert project["handoff_url"] in prompt
        assert "stallsignaal" in prompt
        assert "geen nieuwe generieke checklist" in prompt
        assert "self-hosted GitHub-runner" in prompt
        assert "volgende veilige onafhankelijke ongeclaimde taak" in prompt
