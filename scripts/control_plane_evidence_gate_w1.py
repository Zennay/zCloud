"""Read-only exact-head CI evidence admission gate. No GitHub API or runtime side effects."""
import argparse
import json
import re
import sys

SHA = re.compile(r"^[0-9a-f]{40}$")
CONCLUSIONS = {"success", "failure", "cancelled", "skipped", "timed_out", "action_required", "neutral"}

def assess(document):
    if not isinstance(document, dict):
        return {"accepted": False, "errors": ["manifest must be an object"]}
    errors = []
    head = document.get("head_sha")
    base = document.get("base_sha")
    if not isinstance(head, str) or not SHA.fullmatch(head):
        errors.append("invalid exact head_sha")
    if not isinstance(base, str) or not SHA.fullmatch(base):
        errors.append("invalid base_sha")
    checks = document.get("checks")
    if not isinstance(checks, list) or not checks:
        errors.append("non-empty checks required")
        checks = []
    seen = set()
    for idx, check in enumerate(checks):
        prefix = f"checks[{idx}]"
        if not isinstance(check, dict):
            errors.append(f"{prefix}: expected object")
            continue
        name = check.get("name")
        if not isinstance(name, str) or not name.strip() or name in seen:
            errors.append(f"{prefix}: missing or duplicate check name")
        else:
            seen.add(name)
        sha = check.get("head_sha")
        if sha != head or not isinstance(sha, str) or not SHA.fullmatch(sha):
            errors.append(f"{prefix}: evidence is not exact-head")
        url = check.get("url")
        if not isinstance(url, str) or not re.fullmatch(r"https://github\.com/[^/\s]+/[^/\s]+/actions/runs/[1-9][0-9]*", url):
            errors.append(f"{prefix}: invalid Actions run URL")
        if check.get("status") != "completed" or check.get("conclusion") != "success":
            errors.append(f"{prefix}: not a successful terminal run")
        if check.get("expected_failures", 0) != 0 or type(check.get("expected_failures", 0)) is not int:
            errors.append(f"{prefix}: expected failures do not establish remediation")
    if document.get("collision_free") is not True:
        errors.append("changed-file ownership collision not ruled out")
    if document.get("integration_owner_approved") is not True:
        errors.append("integration owner approval required")
    if document.get("mutation_authorized") is not False:
        errors.append("gate must remain non-authorizing")
    return {"accepted": not errors, "errors": errors}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="local JSON evidence manifest")
    args = parser.parse_args(argv)
    with open(args.manifest, encoding="utf-8") as handle:
        result = assess(json.load(handle))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["accepted"] else 1

if __name__ == "__main__":
    sys.exit(main())
