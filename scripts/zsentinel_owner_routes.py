#!/usr/bin/env python3
"""Canonical owner-gated issue routes shared by zSentinel operator surfaces.

These labels describe the next authorized human owner, not action permission.
No route grants a claim, dispatch, billing change, restart, merge or deploy.
"""
from __future__ import annotations

HUMAN_ONLY_ROUTES = {
    "https://github.com/Zennay/zCloud/issues/1326":
        "authorized_zcloud_runner_owner_required",
    "https://github.com/Zennay/zCloud/issues/1342":
        "authorized_worker_runtime_owner_required",
    "https://github.com/Zennay/zCloud/issues/1523":
        "authorized_github_billing_owner_required",
}
HUMAN_ONLY_ROUTE_CODES = frozenset(HUMAN_ONLY_ROUTES.values()) | frozenset((
    "authorized_infrastructure_owner_required",
))
HUMAN_ONLY_ROUTE_LABELS = {
    "authorized_infrastructure_owner_required": "Menselijke infrablokkade",
    "authorized_zcloud_runner_owner_required": "zCloud-runnerbeheerder nodig",
    "authorized_github_billing_owner_required": "GitHub-billingeigenaar nodig",
    "authorized_worker_runtime_owner_required": "Geserialiseerde runtime-eigenaar nodig",
}
ISSUE_ROUTE_ACTIONS = {
    "authorized_infrastructure_owner_required":
        "Bevoegde infrastructuureigenaar nodig; geen runner-restart, betaling of claim.",
    "authorized_zcloud_runner_owner_required":
        "Bevoegde zCloud-runnerbeheerder verifieert registratie, labels, actuele job en Listener/Worker-status via toegestane bronnen; geen restart zolang actieve worker of identiteit onzeker is, en geen cleanup/reset van een vervuilde checkout zonder goedgekeurd behoud van werk en bewijs.",
    "authorized_github_billing_owner_required":
        "Bevoegde GitHub-accountbillingeigenaar controleert billing/spend-admission; geen betaalde retry, billingwijziging of runneractie.",
    "authorized_worker_runtime_owner_required":
        "De bevoegde geserialiseerde Worker-v2/backend-runtime-eigenaar beoordeelt claim-bound progress-eventhistorie, de scheiding tussen voortgang en heartbeat en UTC-/claimidentiteitsvalidatie. Geen overlappende wijzigingen aan server.py, SQLite-schema, Firefox-extensie of userscript; geen restart, merge of deploy zonder de bestaande eigenaarsgates.",
    "review_existing_pr_first":
        "Begin bij bestaande PR en controleer eigenaar, vereiste CI en actuele scope.",
    "check_assignee_and_current_claim":
        "GitHub heeft een assignee; controleer daarnaast actuele queueclaim, PR's en bestanden.",
    "verify_ownership_and_scope":
        "Controleer actuele claims, branches en bestandscope vóór zelfstandige productimplementatie.",
}


def canonical_issue_action(route):
    if type(route) is not str or route not in ISSUE_ROUTE_ACTIONS:
        raise ValueError("unknown canonical issue action route")
    return ISSUE_ROUTE_ACTIONS[route]
