#!/usr/bin/env python3
"""Canonical owner-gated issue routes shared by zSentinel operator surfaces.

These labels describe the next authorized human owner, not action permission.
No route grants a claim, dispatch, billing change, restart, merge or deploy.
"""
from __future__ import annotations

HUMAN_ONLY_ROUTES = {
    "https://github.com/Zennay/zCloud/issues/1326":
        "authorized_zcloud_runner_owner_required",
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
}
ISSUE_ROUTE_ACTIONS = {
    "authorized_infrastructure_owner_required":
        "Bevoegde infrastructuureigenaar nodig; geen runner-restart, betaling of claim.",
    "authorized_zcloud_runner_owner_required":
        "Bevoegde zCloud-runnerbeheerder verifieert registratie, labels, actuele job en Listener/Worker-status via toegestane bronnen; geen restart zolang actieve worker of identiteit onzeker is.",
    "authorized_github_billing_owner_required":
        "Bevoegde GitHub-accountbillingeigenaar controleert billing/spend-admission; geen betaalde retry, billingwijziging of runneractie.",
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
