"""Core data models for the translation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class UnitStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    FAILED = "failed"
    REUSED = "reused"


class TokenCategory(str, Enum):
    INDEPENDENT = "independent"
    PAIRED = "paired"
    GROUP = "group"
    FIXED = "fixed"


@dataclass(frozen=True)
class ProtectedToken:
    """A protected fragment replaced by an opaque __KEEP_N__ token."""

    token: str
    raw: str
    category: TokenCategory
    group: str | None = None


@dataclass
class TranslationUnit:
    """One translatable unit with its stable identity and context."""

    unit_id: str
    source: str
    source_hash: str
    path: str
    context: dict = field(default_factory=dict)
    tokens: list[ProtectedToken] = field(default_factory=list)
    protected_source: str = ""
    status: UnitStatus = UnitStatus.PENDING
    accepted_target: str | None = None
    generation_id: str = ""

    def to_request(self) -> dict:
        return {
            "id": self.unit_id,
            "source": self.protected_source or self.source,
            "context": self.context,
        }


@dataclass
class Batch:
    """A bounded group of units sent in a single provider request."""

    batch_id: str
    units: list[TranslationUnit]
    attempt: int = 0
    reason: str | None = None
    failure: str | None = None

    @property
    def unit_ids(self) -> set[str]:
        return {u.unit_id for u in self.units}


@dataclass
class GenerationInfo:
    """Immutable identity of one translation generation."""

    generation_id: str
    mode: str
    source_manifest: str
    target_lang: str
    profile_fingerprint: str
    policy_revision: str
    protection_revision: str
    base_generation: str | None
    model_fingerprint: str
    staging: str
    status: str = "active"
    created_at: str = ""


@dataclass
class RunStats:
    """Progress counters for reporting."""

    selected: int = 0
    reused: int = 0
    accepted: int = 0
    pending: int = 0
    failed: int = 0
    waves: int = 0
    batches: int = 0
    retries: int = 0
    retries_by_reason: dict = field(default_factory=dict)