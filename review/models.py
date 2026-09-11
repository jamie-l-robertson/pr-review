"""Output shape for a review. Imported only where pydantic is available —
tool_runner takes a model class, not a raw JSON schema."""
from typing import List, Literal

from pydantic import BaseModel

CATEGORIES = ("security", "cybersecurity", "performance", "accessibility",
              "usability", "code-quality", "testing", "ai-safety",
              "prompt-injection")


class FileVerdict(BaseModel):
    path: str
    verdict: Literal["clean", "defects-reported", "not-reviewed"]


class Finding(BaseModel):
    id: str
    path: str
    line: int
    severity: Literal["blocker", "major", "minor", "nit"]
    category: Literal["security", "cybersecurity", "performance", "accessibility",
                      "usability", "code-quality", "testing", "ai-safety",
                      "prompt-injection"]
    body: str
    remedy: str


class ReviewResult(BaseModel):
    summary: str
    files_reviewed: List[FileVerdict]
    findings: List[Finding]
