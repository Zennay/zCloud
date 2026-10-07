from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED_PATHS = {
    "ftmo-pr500-autonomous-json-proof.yml",
    "ftmo-pr500-json-constants-proof.yml",
    "ftmo-pr501-autonomous-json-proof.yml",
}

RETIRED_MARKERS = {
    'TARGET_PR: "500"',
    'TARGET_PR: "501"',
    "76082e8b5af088c8ba02ed743d467701ecef07f3",
    "cc47aa5d0b6f6e36286506443603ff11d8d55134",
    "ftmo-pr500-autonomous-json-proof",
    "ftmo-pr500-json-constants-proof",
    "ftmo-pr501-autonomous-json-proof",
}


def _workflow_texts():
    for pattern in ("*.yml", "*.yaml"):
        for path in WORKFLOWS.glob(pattern):
            yield path, path.read_text(encoding="utf-8")


def test_terminal_pr500_pr501_proof_workflows_stay_retired():
    present = {path.name for path in WORKFLOWS.iterdir() if path.is_file()}
    assert RETIRED_PATHS.isdisjoint(present)

    hits = []
    for path, text in _workflow_texts():
        for marker in RETIRED_MARKERS:
            if marker in text:
                hits.append((path.name, marker))

    assert not hits, f"retired FTMO PR500/PR501 proof authority reintroduced: {hits}"
