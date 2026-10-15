# Dashboard recovery: read-only incident triage (2026-10-08)

This operator guide covers **observation only** for zCloud dashboard incidents while
[issue #1135](https://github.com/Zennay/zCloud/issues/1135) and the isolated
recovery-authorization work remain open. It does not grant production mutation authority.

## Safety boundary

- **Do not dispatch** `.github/workflows/zcloud-dashboard-access-recovery.yml`
  for diagnosis: on current `main`, its `recover` job runs on `self-hosted` and
  executes `systemctl stop/restart`, `chown`, `chmod`, `chattr`, and systemd drop-in changes.
- A successful hosted `external_verify` is evidence of external HTTP reachability
  at probe time; it is **not** authorization to recover, restart, or change SQLite.
- An unsuccessful hosted probe alone does **not** establish a VPS fault.
  Network path, timeout, stale service, invalid JSON and application status are
  distinct hypotheses. Do not run privileged recovery based on the probe alone.
- Keep the serialized production/control-plane gate (#580/PWQ-41 + #1089)
  closed until its owners explicitly release it using current-main, exact-head proof.
- Never run mutating instructions from an untrusted PR on the self-hosted runner.

## Non-mutating evidence sequence

1. Record incident UTC timestamp, exact commit SHA, workflow URL/run ID, triggering
   event (`push`, `pull_request`, or `workflow_dispatch`), and job conclusions.
   Distinguish the hosted `external_verify` result from any self-hosted `recover` result.
2. From an **independent hosted or operator-controlled client**, issue only a bounded
   HTTP GET to the published `/api/status` endpoint. Capture HTTP status, total
   elapsed time, and whether the JSON contains a nonempty `time` and a `projects`
   array. Do not print secrets or the full response body.
3. If external GET succeeds but a recovery job fails, classify as
   **healthy-external / privileged-recovery-failed**. Do not retry recovery:
   it would repeat potentially disruptive mutations of an apparently working service.
4. If external GET fails, classify initially as **external-unconfirmed**, not
   **service-down**. Compare with existing, trusted, *read-only* service and listener
   telemetry collected under an independently authorized access path. Avoid
   stopping services or editing files to obtain evidence.
5. Record the exact evidence and hand the remediation decision to the owner of
   #1135 / recovery authorization. No automatic promotion from probe failure
   to privileged mutation is permitted.

### Example hosted-only diagnostic

Run in a trusted, non-production environment; this command does not run on the VPS:

```bash
python3 - <<'PY'
import json
import time
import urllib.error
import urllib.request

url = "http://198.244.191.182:8765/api/status"
start = time.monotonic()
try:
    with urllib.request.urlopen(url, timeout=12) as response:
        status = response.status
        # Bound response allocation. The response is never printed.
        payload = response.read(256 * 1024 + 1)
    if len(payload) > 256 * 1024:
        raise ValueError("response_exceeds_limit")
    data = json.loads(payload)
    valid = bool(data.get("time")) and isinstance(data.get("projects"), list)
    outcome = "healthy" if status == 200 and valid else "invalid_status"
except (urllib.error.URLError, TimeoutError, ValueError,
        json.JSONDecodeError) as exc:
    outcome = "unconfirmed_" + type(exc).__name__
print(f"dashboard_external={outcome} elapsed_seconds={time.monotonic() - start:.2f}")
PY
```

**Classification is observational, not an availability SLA.** A successful GET
does not prove SQLite writeability, internal dependency health, or permission to
restart a service. A failed GET never authorizes escalation on its own.

## Recovery re-enablement acceptance

Do not remove the recovery quarantine until a separate, independently reviewed
authorization contract proves all of the following on the **exact candidate head**:

- PR and untrusted contexts are unconditionally denied privileged mutation.
- Healthy external status is denied recovery without regard to local warnings.
- A degraded probe is necessary but insufficient; trusted, fresh internal evidence
  and explicit approved recovery scope are also required.
- Missing, inconsistent, stale, or inconclusive signals fail closed.
- Hosted deny-matrix tests and permitted-path tests pass, and no mutating path
  is exercised during negative testing.
- The production owner explicitly approves the mutation window after the
  serialized #580/#1089 ownership gate clears.

Related: [#1135](https://github.com/Zennay/zCloud/issues/1135),
[#1166](https://github.com/Zennay/zCloud/issues/1166),
[#1177](https://github.com/Zennay/zCloud/issues/1177).
