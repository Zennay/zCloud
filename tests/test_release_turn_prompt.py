from pathlib import Path

import server

ROOT = Path(__file__).resolve().parents[1]


def test_release_turn_prompt_demands_merge_and_live_verification():
    prompt = server.project_release_turn_prompt("cloud", "zCloud")
    assert prompt.startswith("MERGE & LIVE-RONDE voor zCloud")
    assert "GitHub-merge" in prompt and "merged=true" in prompt
    assert "live" in prompt and "main-SHA" in prompt
    assert "Geen bypass, force-merge" in prompt
    assert len(prompt) < 1400


def test_zsentinel_release_turn_targets_real_projects():
    prompt = server.project_release_turn_prompt("zsentinel", "zSentinel")
    assert "ECHTE portfolio-projecten" in prompt


def test_engines_rotate_release_turn_into_continuations():
    assert server.RELEASE_TURN_EVERY >= 2
    for name in ("zcloud-worker-v2-engine.js", "zcloud-worker.user.js"):
        source = (ROOT / "public" / name).read_text(encoding="utf-8")
        assert "releaseTurnDue()" in source, name
        assert "target?.release_turn_every" in source, name
        assert '"zcloud-v2-release-turn:"+id' in source, name
