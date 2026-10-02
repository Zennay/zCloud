from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CapabilityState(str, Enum):
    PLANNING = "planning"
    LAB_ONLY = "lab_only"
    DISABLED = "disabled"


@dataclass(frozen=True)
class Capability:
    capability_id: str
    name: str
    description: str
    state: CapabilityState
    requires_explicit_activation: bool = True


# High-level registry only. No exploit payloads or active target logic live here.
REGISTRY: tuple[Capability, ...] = (
    Capability("web-baseline", "Web baseline", "HTTP/TLS/configuration assessment", CapabilityState.PLANNING),
    Capability("api-baseline", "API baseline", "API exposure and security-control assessment", CapabilityState.PLANNING),
    Capability("network-services", "Network services", "Authorized service inventory and configuration review", CapabilityState.PLANNING),
    Capability("identity-access", "Identity & access", "Authentication and authorization control review", CapabilityState.PLANNING),
    Capability("cloud-iam", "Cloud & IAM", "Cloud configuration and privilege-boundary review", CapabilityState.PLANNING),
    Capability("containers", "Containers", "Container/image/runtime hardening assessment", CapabilityState.PLANNING),
    Capability("supply-chain", "Supply chain", "Dependencies, build inputs and CI/CD security review", CapabilityState.PLANNING),
    Capability("secrets", "Secret exposure", "Secret-handling and accidental exposure assessment", CapabilityState.PLANNING),
    Capability("host-hardening", "Host hardening", "OS/service configuration assessment", CapabilityState.PLANNING),
    Capability("wireless-lab", "Wireless lab", "Isolated wireless-security simulation", CapabilityState.LAB_ONLY),
    Capability("human-layer-lab", "Human-layer lab", "Non-production security-awareness simulation", CapabilityState.LAB_ONLY),
)


def get_capabilities() -> tuple[Capability, ...]:
    return REGISTRY
