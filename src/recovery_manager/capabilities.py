from enum import StrEnum


class CapabilityState(StrEnum):
    DISABLED = "DISABLED"
    SYNTHETIC_ONLY = "SYNTHETIC_ONLY"
    OPERATIONAL_VERIFIED = "OPERATIONAL_VERIFIED"


V01_CAPABILITIES: dict[str, CapabilityState] = {
    "recovery_policy": CapabilityState.DISABLED,
    "official_evidence_contract": CapabilityState.DISABLED,
    "ai_semantic_reasoning": CapabilityState.DISABLED,
    "claim_export": CapabilityState.DISABLED,
    "recovery_ui": CapabilityState.DISABLED,
}
