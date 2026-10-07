from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED_PATHS = {
    "ftmo-pr472-telemetry-proof.yml",
    "ftmo-pr510-permission-hardening-proof.yml",
}

RETIRED_MARKERS = {
    "FTMO PR474 telemetry authority exact-head proof",
    "ftmo-pr474-telemetry-proof",
    "11012541925842d657fd2fee574a4a6100a79efc",
    "FTMO PR510 permission hardening focused proof",
    "ftmo-pr510-permission-hardening-focused-proof",
    "819dc14b045d7ff5d7a3cb3a08a9455cb08aaea7",
}


def _workflow_texts():
    for pattern in ("*.yml", "*.yaml"):
        for path in WORKFLOWS.glob(pattern):
            yield path, path.read_text(encoding="utf-8")


def test_terminal_pr474_pr510_proof_workflows_stay_retired():
    present = {path.name for path in WORKFLOWS.iterdir() if path.is_file()}
    assert RETIRED_PATHS.isdisjoint(present)

    hits = []
    for path, text in _workflow_texts():
        for marker in RETIRED_MARKERS:
            if marker in text:
                hits.append((path.name, marker))

    assert not hits, f"retired FTMO PR474/PR510 proof authority reintroduced: {hits}"
