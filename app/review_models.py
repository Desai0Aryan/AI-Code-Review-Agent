"""Data shapes for the reviewer: what the model may say, and what we are willing to report."""

import hashlib
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, field_validator

CATEGORIES = (
    "bug", "security", "logic", "error-handling", "concurrency", "api-misuse", "test-gap", "other",
)  # fmt: skip
SEVERITIES = ("high", "medium", "low")
CERTAINTIES = ("confirmed", "likely", "uncertain")
STATUS_ORDER = {"confirmed": 0, "likely": 1, "uncertain": 2}


class LLMTraceStep(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    line: int = Field(ge=1)
    note: str = ""


class LLMFinding(BaseModel):
    """One claim from the model. Nothing here is trusted until it passes grounding."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=200)
    category: str = "other"
    severity: str = "medium"
    path: str
    line: int = Field(ge=1)
    end_line: int | None = None
    evidence_quote: str
    explanation: str
    trace: list[LLMTraceStep] = []
    suggested_fix: str | None = None
    confidence: float = 0.5
    certainty: str = "uncertain"
    uncertainty_reason: str | None = None
    based_on: list[str] = []

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        return v if v in CATEGORIES else "other"

    @field_validator("severity")
    @classmethod
    def _severity(cls, v: str) -> str:
        return v if v in SEVERITIES else "medium"

    @field_validator("certainty")
    @classmethod
    def _certainty(cls, v: str) -> str:
        return v if v in CERTAINTIES else "uncertain"

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, v: float) -> float:
        return min(1.0, max(0.0, v))

    @field_validator("end_line")
    @classmethod
    def _end_line(cls, v: int | None, info) -> int | None:
        line = info.data.get("line")
        return v if v is not None and line is not None and v >= line else None


@dataclass
class TraceStep:
    path: str
    line: int
    note: str


@dataclass
class ReviewFinding:
    """A finding we are willing to show a human."""

    id: str
    source: str  # llm | tool | llm+tool
    title: str
    category: str
    severity: str
    path: str
    line: int
    end_line: int | None
    evidence: str  # exact source text (or traceback) backing the claim
    explanation: str
    confidence: float
    status: str  # confirmed | likely | uncertain -- decided by code, never by the model
    scope: str  # changed-line | changed-function | related-test | context
    unit_key: str | None = None
    trace: list[TraceStep] = field(default_factory=list)
    suggested_fix: str | None = None
    uncertainty_reason: str | None = None
    corroborated_by: list[str] = field(
        default_factory=list
    )  # e.g. ["ruff:F821", "pytest:test-failure"]
    relocated_from: int | None = None  # set when the model cited the wrong line number


@dataclass
class RejectedFinding:
    """A model claim that was blocked. Counted: this is the hallucination-prevention metric."""

    unit_key: str
    title: str
    path: str
    line: int
    reason: str


@dataclass
class ReviewResult:
    findings: list[ReviewFinding] = field(default_factory=list)
    rejected: list[RejectedFinding] = field(default_factory=list)
    summaries: dict[str, str] = field(default_factory=dict)  # unit key -> model's one-line verdict
    notes: list[str] = field(default_factory=list)
    units_reviewed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def with_status(self, status: str) -> list[ReviewFinding]:
        return [f for f in self.findings if f.status == status]


def make_id(path: str, line: int, category: str) -> str:
    return hashlib.sha1(f"{path}:{line}:{category}".encode()).hexdigest()[:10]
