from __future__ import annotations

import argparse
import json

from .models import Target
from .orchestrator import Planner
from .scope import ScopePolicy


def _policy(args: argparse.Namespace) -> ScopePolicy:
    return ScopePolicy(
        allow_private_lab=not args.no_private_lab,
        explicit_hosts=frozenset(args.allow_host or []),
        explicit_networks=tuple(args.allow_cidr or []),
        require_authorization_for_public=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lightup")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--allow-host", action="append", default=[])
    shared.add_argument("--allow-cidr", action="append", default=[])
    shared.add_argument("--no-private-lab", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)
    scope = sub.add_parser("scope-check", parents=[shared])
    scope.add_argument("target")

    plan = sub.add_parser("plan", parents=[shared])
    plan.add_argument("target")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    planner = Planner(_policy(args))
    target = Target(args.target)
    plan = planner.build(target)

    if args.command == "scope-check":
        print(json.dumps({
            "allowed": plan.scope.allowed,
            "host": plan.scope.normalized_host,
            "reason": plan.scope.reason.value,
        }, indent=2))
        return 0 if plan.scope.allowed else 2

    print(json.dumps(plan.to_dict(), indent=2))
    return 0 if plan.scope.allowed else 2


if __name__ == "__main__":
    raise SystemExit(main())
