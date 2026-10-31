"""Canonical typed data model for Beeja.

Every decision fact lives in these Pydantic models and is serialized as JSON.
Missing source metadata stays ``None``; it is never invented.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


# ---------------------------------------------------------------- sources


class SourceMessage(_Model):
    """One message of a discussion, preserved as close to the original as possible."""

    message_id: str
    thread_id: str
    platform: str = "unknown"
    source_id: Optional[str] = None
    parent_message_id: Optional[str] = None
    reply_to: Optional[str] = None
    author_id: Optional[str] = None
    author_display_name: Optional[str] = None
    author_role: Optional[str] = None
    created_at: Optional[str] = None
    source_url: Optional[str] = None
    body: str
    source_snapshot_hash: Optional[str] = None
    position: int = 0


class Discussion(_Model):
    thread_id: str
    title: Optional[str] = None
    platform: str = "unknown"
    source_url: Optional[str] = None
    messages: list[SourceMessage]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def message(self, message_id: str) -> Optional[SourceMessage]:
        for m in self.messages:
            if m.message_id == message_id:
                return m
        return None


# ---------------------------------------------------------------- decisions


class AlternativeStatus(str, Enum):
    proposed = "proposed"
    accepted = "accepted"
    rejected = "rejected"
    withdrawn = "withdrawn"
    deferred = "deferred"
    unresolved = "unresolved"
    superseded = "superseded"


class DecisionStatus(str, Enum):
    decided = "decided"
    partially_decided = "partially_decided"
    unresolved = "unresolved"
    superseded = "superseded"


class RejectionType(str, Enum):
    explicit = "explicit"
    inferred = "inferred"


class Verification(str, Enum):
    """Result of checking a cited passage against the stored source."""

    exact = "exact"  # passage found verbatim (after whitespace normalisation)
    approximate = "approximate"  # close fuzzy match, needs human review
    not_found = "not_found"  # passage does not occur in the cited message
    missing_message = "missing_message"  # cited message id does not exist
    unchecked = "unchecked"


class Support(str, Enum):
    """Whether a verified passage actually supports the claim it is attached to."""

    supports = "supports"
    weak = "weak"
    does_not_support = "does_not_support"
    unchecked = "unchecked"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    uncertain = "uncertain"


class Evidence(_Model):
    evidence_id: str
    message_id: str
    quote: str
    source_url: Optional[str] = None
    author: Optional[str] = None
    created_at: Optional[str] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    source_hash: Optional[str] = None
    verification: Verification = Verification.unchecked
    support: Support = Support.unchecked
    archive_url: Optional[str] = None


class Premise(_Model):
    """An optional, possibly machine-checkable condition underlying a reason."""

    description: str
    premise_type: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    verified: bool = False


class Reason(_Model):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    premise: Optional[Premise] = None
    supported: bool = False


class Alternative(_Model):
    alternative_id: str
    title: str
    description: str = ""
    status: AlternativeStatus = AlternativeStatus.proposed
    rejection_type: Optional[RejectionType] = None
    reasons: list[Reason] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    opposing_evidence: list[str] = Field(default_factory=list)
    status_evidence: list[str] = Field(default_factory=list)
    status_support: Support = Support.unchecked  # support-check verdict for *this* outcome claim
    proponents: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.uncertain
    mentions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CodeAnchor(_Model):
    kind: str  # "path" | "symbol" | "pr" | "issue" | "commit" | "keyword"
    value: str
    repository: Optional[str] = None
    note: Optional[str] = None


class DecisionRecord(_Model):
    schema_version: str = SCHEMA_VERSION
    id: str
    question: str
    summary: str = ""
    topic: str = ""
    status: DecisionStatus = DecisionStatus.unresolved
    alternatives: list[Alternative] = Field(default_factory=list)
    reasons: list[Reason] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    code_anchors: list[CodeAnchor] = Field(default_factory=list)
    source_threads: list[str] = Field(default_factory=list)
    decision_authority: Optional[str] = None
    confidence: Confidence = Confidence.uncertain
    decided_at: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    presentation: Optional[list[dict[str, Any]]] = None  # optional Portable Text blocks

    def evidence_by_id(self) -> dict[str, Evidence]:
        return {e.evidence_id: e for e in self.evidence}

    def alternative(self, alternative_id: str) -> Optional[Alternative]:
        for a in self.alternatives:
            if a.alternative_id == alternative_id:
                return a
        return None


def check_integrity(record: DecisionRecord) -> list[str]:
    """Structural checks beyond what the type system enforces.

    Returns a list of human-readable problems; empty means the record is consistent.
    """
    problems: list[str] = []
    ev_ids = [e.evidence_id for e in record.evidence]
    if len(ev_ids) != len(set(ev_ids)):
        problems.append("duplicate evidence_id values")
    known = set(ev_ids)
    alt_ids = [a.alternative_id for a in record.alternatives]
    if len(alt_ids) != len(set(alt_ids)):
        problems.append("duplicate alternative_id values")

    def _refs(where: str, ids: list[str]) -> None:
        for i in ids:
            if i not in known:
                problems.append(f"{where} references unknown evidence {i!r}")

    for r in record.reasons:
        _refs("decision reason", r.evidence_ids)
    for a in record.alternatives:
        for r in a.reasons:
            _refs(f"alternative {a.alternative_id} reason", r.evidence_ids)
        _refs(f"alternative {a.alternative_id} supporting_evidence", a.supporting_evidence)
        _refs(f"alternative {a.alternative_id} opposing_evidence", a.opposing_evidence)
        _refs(f"alternative {a.alternative_id} status_evidence", a.status_evidence)
        if a.status == AlternativeStatus.rejected.value and a.rejection_type is None:
            problems.append(f"alternative {a.alternative_id} is rejected without a rejection_type")
    return problems


def json_schema() -> dict[str, Any]:
    return DecisionRecord.model_json_schema()
