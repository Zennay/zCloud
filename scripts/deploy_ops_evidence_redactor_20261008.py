"""Offline deploy evidence redactor. Never grants deploy or recovery permission.

Usage: python3 scripts/deploy_ops_evidence_redactor_20261008.py < receipt.json
Reads one JSON object from stdin and emits an intentionally minimal allowlisted receipt.
No subprocess, network, file writes, GitHub API, or service access.
"""
import json
import re
import sys

SHA = re.compile(r"^[a-fA-F0-9]{40}$")
STATUS = {"success", "failure", "cancelled", "skipped", "unknown"}
MAX_INPUT_BYTES = 65536\nFIELDS = ("repository", "candidate_sha", "main_sha", "regression", "vps_probe", "production_receipt")
def redact(value):
    if not isinstance(value, dict):
        raise ValueError("receipt must be an object")
    candidate = value.get("candidate_sha")
    main = value.get("main_sha")
    if not isinstance(candidate, str) or not SHA.fullmatch(candidate):
        raise ValueError("invalid candidate SHA")
    if not isinstance(main, str) or not SHA.fullmatch(main):
        raise ValueError("invalid main SHA")
    if value.get("repository") != "Zennay/zCloud":
        raise ValueError("unexpected repository")
    out = {"repository": "Zennay/zCloud", "candidate_sha": candidate.lower(), "main_sha": main.lower()}
    for key in FIELDS[3:]:
        record = value.get(key)
        if not isinstance(record, dict):
            raise ValueError("missing evidence object: " + key)
        status = record.get("status")
        if status not in STATUS:
            raise ValueError("invalid evidence status: " + key)
        out[key] = {"status": status}
    out["release_authorized"] = False
    out["recovery_authorized"] = False
    out["mutation_performed"] = False
    return out

def reject_duplicates(pairs):\n    output = {}\n    for key, value in pairs:\n        if key in output:\n            raise ValueError("duplicate JSON key")\n        output[key] = value\n    return output\n\ndef main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)\n        if len(raw) > MAX_INPUT_BYTES:\n            raise ValueError("receipt exceeds size limit")\n        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
        print(json.dumps(redact(payload), sort_keys=True))
    except (ValueError, TypeError, UnicodeError) as exc:
        print(json.dumps({"error": "invalid evidence", "release_authorized": False,
                          "recovery_authorized": False, "mutation_performed": False}), file=sys.stdout)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
