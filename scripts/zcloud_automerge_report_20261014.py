#!/usr/bin/env python3
"""Non-authorizing, bounded human-readable summary of zCloud source integration."""
from collections import Counter
import json
from pathlib import Path
import re
import sys

MAX_REPORT_BYTES = 262144
PROVENANCE = "Zennay/zCloud"
SAFE_DECISION = re.compile(r"^[a-z_]{1,50}$")


class InvalidReport(ValueError):
    pass


def _integer(value, upper=1000):
    if type(value) is not int or not 0 <= value <= upper:
        raise InvalidReport("invalid_count")
    return value


def summary(report):
    if type(report) is not dict or report.get("repo") != PROVENANCE:
        raise InvalidReport("untrusted_repo")
    mode = report.get("mode")
    if mode not in ("apply", "dry_run"):
        raise InvalidReport("untrusted_mode")
    inv = report.get("inventory")
    rec = report.get("receipts")
    if type(inv) is not dict or type(rec) is not list or len(rec) > 1000:
        raise InvalidReport("untrusted_inventory")
    scanned = _integer(inv.get("open_prs_scanned"))
    labeled = _integer(inv.get("explicitly_labeled"))
    draft = _integer(inv.get("draft_prs"))
    review = _integer(inv.get("unlabeled_non_draft"))
    if labeled > scanned or draft > scanned or review > scanned or review > scanned - labeled:
        raise InvalidReport("inconsistent_inventory")
    ids = inv.get("review_only_pr_ids")
    if type(ids) is not list or len(ids) > 10 or len(set(str(x) for x in ids)) != len(ids):
        raise InvalidReport("untrusted_review_ids")
    if any(type(n) is not int or not 0 < n <= 10000000 for n in ids):
        raise InvalidReport("untrusted_review_ids")
    reasons = Counter()
    seen = set()
    merged = 0
    for item in rec:
        if type(item) is not dict:
            raise InvalidReport("untrusted_receipt")
        number, reason = item.get("pr"), item.get("decision")
        if type(number) is not int or not 0 < number <= 10000000 or number in seen:
            raise InvalidReport("untrusted_receipt")
        if type(reason) is not str or SAFE_DECISION.fullmatch(reason) is None:
            raise InvalidReport("untrusted_reason")
        seen.add(number)
        reasons[reason] += 1
        if reason == "merged":
            merged += 1
            if type(item.get("merge_sha")) is not str or re.fullmatch(
                    r"[0-9a-f]{40}", item["merge_sha"]) is None:
                raise InvalidReport("untrusted_merge")
    if len(rec) > labeled or merged > 1 or (mode == "dry_run" and merged != 0):
        raise InvalidReport("inconsistent_receipts")
    lines = [
        "## zCloud autonome PR-integratie — bronoverzicht", "",
        f"- **Modus:** `{mode}`",
        f"- **Open PR's gecontroleerd:** {scanned}",
        f"- **Expliciet toegelaten (`zcloud-automerge-ready`):** {labeled}",
        f"- **Concept-PR's:** {draft}",
        f"- **PR's zonder label (alleen bekijken):** {review}",
        f"- **Door de integrator gerapporteerde bronmerges:** {merged}",
        "- **Live workers, VPS-runner, productie en SQLite:** niet gecontroleerd",
        "- **Nieuwe goedkeuringen vanuit dit rapport:** geen",
    ]
    if not labeled:
        lines.append("- **Vervolg:** geen PR heeft het verplichte expliciete label."
                     " Controleer eigenaarschap, broncode en tests eerst; keur"
                     " alleen de bedoelde PR daarna afzonderlijk goed.")
    if ids:
        lines.append("- **Recente PR-nummers zonder label (handmatig beoordelen,"
                     " NIET goedgekeurd voor merge):** " + ", ".join(
                         f"#{n}" for n in ids))
    if reasons:
        lines.extend(["", "| Besluitcode | Aantal |", "| --- | ---: |"])
        for reason, count in sorted(reasons.items()):
            lines.append(f"| `{reason}` | {count} |")
    lines.extend(["", "Dit is een door de integrator opgesteld bronoverzicht."
                  " De gemelde merge is hier niet onafhankelijk via GitHub"
                  " herbevestigd. Een GitHub-hosted test bewijst **geen**"
                  " live tekstgeneratie, werkende VPS-runner of productie-uitrol."])
    return "\n".join(lines) + "\n"


def read_report(path):
    raw = Path(path).read_bytes()
    if len(raw) > MAX_REPORT_BYTES:
        raise InvalidReport("oversized_report")
    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise InvalidReport("duplicate_json_key")
            out[k] = v
        return out
    try:
        report = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(
                                InvalidReport("invalid_number")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidReport("invalid_report") from exc
    return summary(report)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        return 2
    valid = True
    try:
        result = read_report(args[0])
    except (InvalidReport, OSError):
        valid = False
        result = ("## zCloud PR-integratie — bewijs ontbreekt of is ongeldig\n\n"
                  "Deze samenvatting bevestigt geen bronmerge of productie-uitrol.\n")
    with Path(args[1]).open("a", encoding="utf-8") as target:
        target.write(result)
    return 0 if valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
