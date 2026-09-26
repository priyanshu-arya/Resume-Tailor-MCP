"""Formal schemas -- the single field vocabulary for every module (spec §11).

Nothing else in `lib/` should invent its own field names for masters,
versions, evidence, templates, validation or release reports. Models are
pydantic v2. Resume-body models allow extra keys so that legacy fields
(e.g. an `academic: true` flag on a project, or a parser-added key) survive
a round trip instead of being silently dropped.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from lib.errors import ResumeTailorError

SCHEMA_VERSION = 2

# --------------------------------------------------------------------------
# Kinds
# --------------------------------------------------------------------------

DOCUMENT_KINDS = ("resume", "cv")


def validate_kind(kind: str) -> str:
    """Central `kind` validation (spec §10). Every master-touching path calls this."""
    if kind not in DOCUMENT_KINDS:
        raise ResumeTailorError("INVALID_KIND", f"kind must be 'resume' or 'cv', got {kind!r}")
    return kind


# --------------------------------------------------------------------------
# Evidence taxonomy (spec §15)
# --------------------------------------------------------------------------

class EvidenceCategory(str, Enum):
    professional = "professional"
    internship = "internship"
    personal_project = "personal_project"
    academic = "academic"
    coursework = "coursework"
    certification = "certification"
    learning_only = "learning_only"
    none = "none"


# A claim's strength uses the same vocabulary as the evidence behind it.
ClaimStrength = EvidenceCategory

# Allowed-claim order -- NOT a ranking of the candidate. personal_project and
# academic are deliberately equal.
CLAIM_RANK: dict[str, int] = {
    "professional": 7,
    "internship": 6,
    "personal_project": 5,
    "academic": 5,
    "coursework": 4,
    "certification": 3,
    "learning_only": 2,
    "none": 0,
}

# Category of a *master* block is derived from its section (see lib/ids.py).
# Master skill items get their own pseudo-category: they may only support
# claims in the Skills section.
MASTER_SKILL_CATEGORY = "master_skill"


def claim_rank(category: str) -> int:
    if category == MASTER_SKILL_CATEGORY:
        return CLAIM_RANK["professional"]
    return CLAIM_RANK[category]


# --------------------------------------------------------------------------
# Resume body (shared by masters and versions)
# --------------------------------------------------------------------------

class SourceRef(BaseModel):
    type: Literal["master", "evidence"]
    id: str


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Bullet(_Open):
    id: str | None = None
    text: str
    source_refs: list[SourceRef] = Field(default_factory=list)
    claim_strength: ClaimStrength | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillItem(_Open):
    id: str | None = None
    name: str
    source_refs: list[SourceRef] = Field(default_factory=list)
    claim_strength: ClaimStrength | None = None


class SkillGroup(_Open):
    category: str = "General"
    items: list[SkillItem] = Field(default_factory=list)


class Experience(_Open):
    id: str | None = None
    title: str = ""
    company: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    bullets: list[Bullet] = Field(default_factory=list)


class Project(_Open):
    id: str | None = None
    name: str = ""
    github: str | None = None
    stack: str | None = None
    dates: str | None = None
    academic: bool = False
    bullets: list[Bullet] = Field(default_factory=list)


class Education(_Open):
    id: str | None = None
    degree: str = ""
    school: str = ""
    year: str = ""
    bullets: list[Bullet] = Field(default_factory=list)


class Certification(_Open):
    id: str | None = None
    text: str
    source_refs: list[SourceRef] = Field(default_factory=list)
    claim_strength: ClaimStrength | None = None


# Fixed ID of the (single) summary block.
SUMMARY_ID = "sum-001"


class ResumeBody(_Open):
    name: str = ""
    contact: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    skills: list[SkillGroup] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    unparsed: list[Any] = Field(default_factory=list)


class MigrationInfo(BaseModel):
    source: Literal["legacy_repository"] = "legacy_repository"
    source_hash: str
    migrated_at: str
    legacy_path: str


class MasterMetadata(_Open):
    kind: Literal["resume", "cv"]
    schema_version: int = SCHEMA_VERSION
    career_stage: str | None = None  # fresher | 1-3 | 3-5 | 5-10 | manager | director | academic
    unparsed_accepted: bool = False
    migration: MigrationInfo | None = None


class MasterDocument(ResumeBody):
    metadata: MasterMetadata


class VersionMetadata(_Open):
    version_id: str
    workspace_id: str
    workflow_id: str | None = None
    source_master_hash: str | None = None
    document_kind: Literal["resume", "cv"]
    template_id: str | None = None
    template_version: str | None = None
    rules_version: str | None = None
    jd_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str
    released: bool = False
    release_report_id: str | None = None
    legacy: bool = False
    repair_of: str | None = None
    unknown_jd_requirements: list[str] = Field(default_factory=list)
    summary_source_refs: list[SourceRef] = Field(default_factory=list)
    summary_claim_strength: ClaimStrength | None = None


class TailoredVersion(ResumeBody):
    metadata: VersionMetadata


# --------------------------------------------------------------------------
# Evidence (spec §20)
# --------------------------------------------------------------------------

class TailoringEvidence(BaseModel):
    id: str
    workflow_id: str
    workspace_id: str
    term: str
    category: EvidenceCategory
    evidence_text: str
    confirmed: bool
    metrics: list[str] = Field(default_factory=list)
    created_at: str


# --------------------------------------------------------------------------
# Structured patches from Claude (spec §13) -- UNTRUSTED input
# --------------------------------------------------------------------------

class PatchTarget(BaseModel):
    id: str


class NewContent(BaseModel):
    text: str
    source_refs: list[SourceRef] = Field(default_factory=list)
    claim_strength: ClaimStrength | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplaceBlock(BaseModel):
    operation: Literal["replace_block"]
    target: PatchTarget
    new_content: NewContent


class DropBlock(BaseModel):
    operation: Literal["drop_block"]
    target: PatchTarget


class Reorder(BaseModel):
    operation: Literal["reorder"]
    # exactly one of parent_id (bullets inside an entry) / section (entries
    # or skill groups inside a top-level section)
    parent_id: str | None = None
    section: str | None = None
    order: list[str]


class AddBlock(BaseModel):
    operation: Literal["add_block"]
    parent_id: str
    new_content: NewContent


class AddSkillItem(BaseModel):
    operation: Literal["add_skill_item"]
    category: str
    name: str
    source_refs: list[SourceRef] = Field(default_factory=list)
    claim_strength: ClaimStrength | None = None


Patch = Annotated[
    Union[ReplaceBlock, DropBlock, Reorder, AddBlock, AddSkillItem],
    Field(discriminator="operation"),
]

REPAIR_SAFE_OPERATIONS = ("drop_block", "reorder")


# --------------------------------------------------------------------------
# Templates (spec §27, §29)
# --------------------------------------------------------------------------

class TemplateStatus(str, Enum):
    supported = "supported"
    experimental = "experimental"
    unsupported = "unsupported"


class TemplateContract(_Open):
    id: str
    name: str
    status: TemplateStatus
    version: str
    page: dict[str, Any] | str = "unknown"
    typography: dict[str, Any] | str = "unknown"
    layout: dict[str, Any] | str = "unknown"
    sections: dict[str, Any] | str = "unknown"
    spacing: dict[str, Any] | str = "unknown"
    limits: dict[str, Any] | str = "unknown"
    formatting: dict[str, Any] | str = "unknown"


# --------------------------------------------------------------------------
# Validation / release (spec §38, §40)
# --------------------------------------------------------------------------

CheckStatus = Literal["pass", "fail", "warning", "not_available"]
Severity = Literal["critical", "error", "warning", "info"]


class Check(BaseModel):
    id: str
    status: CheckStatus
    severity: Severity
    category: Literal["SOURCE", "FACTUAL", "TEMPLATE", "FORMAT", "LATEX_PDF", "WORKFLOW"]
    measurement: Any = None
    expected: Any = None
    source: str  # e.g. "template_contract", "etiquette", "pdf_measured", "tex_inferred"
    message: str = ""

    @property
    def blocking(self) -> bool:
        return self.status == "fail" and self.severity in ("critical", "error")


class ValidationReport(BaseModel):
    version_id: str
    workflow_id: str | None = None
    template_id: str | None = None
    checks: list[Check] = Field(default_factory=list)
    measured_properties: dict[str, Any] = Field(default_factory=dict)
    inferred_properties: dict[str, Any] = Field(default_factory=dict)
    not_available: list[str] = Field(default_factory=list)
    tex_sha256: str | None = None
    pdf_sha256: str | None = None

    @property
    def critical_failures(self) -> list[Check]:
        return [c for c in self.checks if c.blocking]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == "warning" or (c.status == "fail" and not c.blocking)]

    @property
    def passed(self) -> bool:
        return not self.critical_failures


class ReleaseReport(BaseModel):
    release_report_id: str
    version_id: str
    workspace_id: str
    workflow_id: str | None = None
    released: bool
    created_at: str
    template_id: str | None = None
    template_version: str | None = None
    rules_version: str | None = None
    source_master_hash: str | None = None
    tex_sha256: str | None = None
    pdf_sha256: str | None = None
    checks: list[Check] = Field(default_factory=list)
    critical_failures: list[str] = Field(default_factory=list)  # check ids
    warnings: list[str] = Field(default_factory=list)  # check ids
    measured_properties: dict[str, Any] = Field(default_factory=dict)
    inferred_properties: dict[str, Any] = Field(default_factory=dict)
    not_available: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Workflow (spec §48)
# --------------------------------------------------------------------------

class WorkflowMetadata(_Open):
    workflow_id: str
    workspace_id: str
    source_kind: Literal["resume", "cv"]
    jd_id: str | None = None
    jd_sha256: str | None = None
    created_at: str
    evidence_ids: list[str] = Field(default_factory=list)
    version_ids: list[str] = Field(default_factory=list)
    repair_attempts: int = 0
    status: Literal["analyzing", "tailored", "validated", "released", "blocked"] = "analyzing"
    analysis: dict[str, Any] = Field(default_factory=dict)
