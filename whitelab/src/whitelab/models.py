from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RetestStatus(str, Enum):
    NOT_TESTED = "not_tested"
    FIX_PENDING = "fix_pending"
    FIXED = "fixed"
    REGRESSION = "regression"


@dataclass(frozen=True)
class Authorization:
    owner: str
    reference: str
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    def is_current(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("authorization time must be timezone-aware")
        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        return True


@dataclass(frozen=True)
class Target:
    value: str
    authorization: Authorization | None = None
    labels: tuple[str, ...] = ()


@dataclass
class Finding:
    finding_id: str
    title: str
    severity: Severity
    target: str
    evidence: list[str] = field(default_factory=list)
    remediation: str = ""
    retest_status: RetestStatus = RetestStatus.NOT_TESTED
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.finding_id.strip():
            raise ValueError("finding_id is required")
        if not self.title.strip():
            raise ValueError("title is required")
        if not self.target.strip():
            raise ValueError("target is required")
        if not self.remediation.strip():
            raise ValueError("remediation is required")
