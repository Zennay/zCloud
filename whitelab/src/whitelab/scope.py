from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

from .models import Target


class ScopeReason(str, Enum):
    LOOPBACK = "loopback"
    PRIVATE_LAB = "private_lab"
    EXPLICIT_HOST = "explicit_host"
    EXPLICIT_NETWORK = "explicit_network"
    AUTHORIZATION_MISSING = "authorization_missing"
    AUTHORIZATION_EXPIRED = "authorization_expired"
    OUT_OF_SCOPE = "out_of_scope"
    INVALID_TARGET = "invalid_target"


@dataclass(frozen=True)
class ScopeDecision:
    allowed: bool
    normalized_host: str | None
    reason: ScopeReason


@dataclass(frozen=True)
class ScopePolicy:
    allow_private_lab: bool = True
    explicit_hosts: frozenset[str] = frozenset()
    explicit_networks: tuple[str, ...] = ()
    require_authorization_for_public: bool = True

    def _networks(self):
        return tuple(ip_network(item, strict=False) for item in self.explicit_networks)

    @staticmethod
    def normalize_host(value: str) -> str | None:
        raw = value.strip()
        if not raw:
            return None
        parsed = urlparse(raw if "://" in raw else f"//{raw}", scheme="")
        host = parsed.hostname
        if not host:
            return None
        return host.rstrip(".").lower()

    def decide(self, target: Target) -> ScopeDecision:
        host = self.normalize_host(target.value)
        if host is None:
            return ScopeDecision(False, None, ScopeReason.INVALID_TARGET)

        if host == "localhost":
            return ScopeDecision(True, host, ScopeReason.LOOPBACK)

        try:
            addr = ip_address(host)
        except ValueError:
            addr = None

        if addr is not None:
            if addr.is_loopback:
                return ScopeDecision(True, host, ScopeReason.LOOPBACK)
            if self.allow_private_lab and (addr.is_private or addr.is_link_local):
                return ScopeDecision(True, host, ScopeReason.PRIVATE_LAB)
            if any(addr in network for network in self._networks()):
                if self.require_authorization_for_public:
                    if target.authorization is None:
                        return ScopeDecision(False, host, ScopeReason.AUTHORIZATION_MISSING)
                    if not target.authorization.is_current():
                        return ScopeDecision(False, host, ScopeReason.AUTHORIZATION_EXPIRED)
                return ScopeDecision(True, host, ScopeReason.EXPLICIT_NETWORK)
            return ScopeDecision(False, host, ScopeReason.OUT_OF_SCOPE)

        if host in {item.rstrip(".").lower() for item in self.explicit_hosts}:
            if self.require_authorization_for_public:
                if target.authorization is None:
                    return ScopeDecision(False, host, ScopeReason.AUTHORIZATION_MISSING)
                if not target.authorization.is_current():
                    return ScopeDecision(False, host, ScopeReason.AUTHORIZATION_EXPIRED)
            return ScopeDecision(True, host, ScopeReason.EXPLICIT_HOST)

        return ScopeDecision(False, host, ScopeReason.OUT_OF_SCOPE)
