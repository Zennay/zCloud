# Saved worker-scaling handoff

This offline CLI turns an already-saved JSON response from the zCloud `GET /api/worker-scaling` endpoint into a private static HTML, Markdown, or JSON handoff.

## Usage

Create an owner-private directory and save the API response as a private file:

```sh
install -d -m 700 "$HOME/.local/share/zsentinel"
chmod 600 "$HOME/.local/share/zsentinel/worker-scaling.json"
python3 scripts/zsentinel_saved_worker_scaling.py \
  --input "$HOME/.local/share/zsentinel/worker-scaling.json" \
  --output "$HOME/.local/share/zsentinel/worker-scaling-handoff.html" \
  --format html
```

To compare a newer receipt with an earlier saved receipt, pass the older JSON via `--previous` and keep `--input` as the newer observation:

```sh
python3 scripts/zsentinel_saved_worker_scaling.py \
  --input "$HOME/.local/share/zsentinel/worker-scaling-current.json" \
  --previous "$HOME/.local/share/zsentinel/worker-scaling-previous.json" \
  --output "$HOME/.local/share/zsentinel/worker-scaling-comparison.html" \
  --format html
```

The input must be an absolute path to a regular, owner-private file inside an owner-private directory. Input and output are each capped at 1 MiB. Reads use no-follow descriptors and verify file identity before and after reading. Output is create-only, mode 0600, and never overwrites an existing file.

## Evidence contract

- Preserves the exact source SHA-256 and original observation timestamp; reports fresh, stale, clock-skewed, and future-dated receipts distinctly.
- Validates source coverage, project and worker identities, required metric fields, timezone-aware clocks, cardinality limits, duplicate JSON keys, and finite numbers.
- Missing desired-worker configuration stays unknown and cannot be used to claim complete scaling evidence. Missing alert evidence stays unknown rather than becoming a zero.
- Reports using the versioned non-authorizing worker-scaling contract must explicitly set all authorization and active-claim flags to false. Older receipts remain viewable but are labelled legacy-unverified and cannot mark the assessment context complete.
- Optional `--previous` creates a descriptive snapshot comparison only when both receipts have complete, versioned source coverage, strictly ordered trustworthy timestamps, matching per-project worker topology, and fresh per-worker heartbeats whose timestamp-derived age agrees with the recorded age. It reports throughput/completion/working/idle/blocked deltas; changed topology or incomplete sources remain partial/inconclusive rather than becoming a scaling recommendation.
- A previous snapshot may be stale relative to now and still serve as a historical baseline. If the current snapshot is stale or clock-uncertain, the output is labelled historical-only; it never attributes causality or authorizes a scale change.
- The generated HTML is static, script-free, and has a restrictive content-security policy. Markdown and JSON exports contain only bounded, validated fields.

This is historical, read-only evidence only. It makes no network requests and does not read or write SQLite, Notion, GitHub, queues, claims, worker controls, or deployment state. It never authorizes claim, dispatch, scaling change, merge, or deployment.
