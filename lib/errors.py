"""One error model for every tool.

Business/validation failures raise `ResumeTailorError`; the server turns
them into `{"ok": false, "error": {...}}` results. Anything else is an
unexpected internal error and is reported as a generic INTERNAL_ERROR so
filesystem paths and personal data never leak into an MCP response.

Every code maps to one failure category (spec §50) so audit events and
metrics can be grouped without inventing a new taxonomy per module.
"""

from __future__ import annotations

# code -> failure category
ERROR_CATEGORIES: dict[str, str] = {
    # workspace / storage
    "WORKSPACE_NOT_INITIALIZED": "WORKFLOW",
    "WORKSPACE_EXISTS": "WORKFLOW",
    "INVALID_ID": "SOURCE",
    "PATH_TRAVERSAL": "SOURCE",
    "INVALID_KIND": "SOURCE",
    "LOCK_TIMEOUT": "WORKFLOW",
    "MASTER_NOT_FOUND": "SOURCE",
    "MASTER_CONFLICT": "SOURCE",
    "MASTER_INVALID": "SOURCE",
    "MASTER_EXISTS": "SOURCE",
    "CONFIRMATION_REQUIRED": "WORKFLOW",
    "VERSION_NOT_FOUND": "SOURCE",
    "VERSION_EXISTS": "WORKFLOW",
    "VERSION_RELEASED": "WORKFLOW",
    "YAML_UNSAFE": "SOURCE",
    "YAML_TOO_LARGE": "SOURCE",
    "YAML_TOO_DEEP": "SOURCE",
    "IMPORT_TOO_LARGE": "SOURCE",
    "IMPORT_UNSUPPORTED": "SOURCE",
    "MIGRATION_CONFLICT": "WORKFLOW",
    "MIGRATION_NO_LEGACY": "WORKFLOW",
    # tailoring / provenance
    "PATCH_INVALID": "FACTUAL",
    "PROVENANCE_VIOLATION": "FACTUAL",
    "EVIDENCE_INVALID": "FACTUAL",
    "EVIDENCE_NOT_FOUND": "FACTUAL",
    "WORKFLOW_NOT_FOUND": "WORKFLOW",
    "WORKFLOW_REQUIRED": "WORKFLOW",
    "REPAIR_LIMIT": "WORKFLOW",
    # templates / release
    "TEMPLATE_UNKNOWN": "TEMPLATE",
    "TEMPLATE_NO_RENDERER": "TEMPLATE",
    "TEMPLATE_NOT_RELEASABLE": "TEMPLATE",
    "VALIDATION_FAILED": "FORMAT",
    "PDF_VALIDATION_UNAVAILABLE": "LATEX_PDF",
    "LATEX_COMPILE_FAILED": "LATEX_PDF",
    "NOT_RELEASED": "WORKFLOW",
    "RULES_UNAVAILABLE": "WORKFLOW",
    "INTERNAL_ERROR": "WORKFLOW",
}

FAILURE_CATEGORIES = ("SOURCE", "FACTUAL", "TEMPLATE", "FORMAT", "LATEX_PDF", "WORKFLOW")


class ResumeTailorError(Exception):
    """A predictable business/validation failure with a stable code."""

    def __init__(self, code: str, message: str, *, severity: str = "error", details: dict | None = None):
        if code not in ERROR_CATEGORIES:
            raise ValueError(f"Unregistered error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.severity = severity
        self.details = details or {}

    @property
    def category(self) -> str:
        return ERROR_CATEGORIES[self.code]

    def to_result(self) -> dict:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "category": self.category,
                "message": self.message,
                "severity": self.severity,
                "details": self.details,
            },
        }


def internal_error_result() -> dict:
    return {
        "ok": False,
        "error": {
            "code": "INTERNAL_ERROR",
            "category": "WORKFLOW",
            "message": "Unexpected internal error. Details were logged locally.",
            "severity": "critical",
            "details": {},
        },
    }
