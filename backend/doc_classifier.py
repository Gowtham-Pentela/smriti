"""
backend/doc_classifier.py
──────────────────────────
Heuristic document category classifier.

Takes a filename and optional first-line content and returns a DocumentCategory
string. Used at index time to tag vector_chunks with their document type so
users can filter answers by category ("show only deployment docs").

Categories (stored in vector_chunks.document_category):
  requirements   — PRDs, user stories, feature requests, acceptance criteria
  specification  — Technical specs, API contracts, data models, RFCs
  release_notes  — Changelogs, release notes, version histories
  deployment     — Runbooks, deployment guides, CI/CD configs, Dockerfiles
  architecture   — ADRs, system design docs, diagrams
  meeting        — Meeting notes, action items, retrospectives
  slack          — Slack/Teams messages (set by slack_connector, not this module)
  general        — Everything else (fallback)
"""

import os
import re
from enum import Enum


class DocCategory(str, Enum):
    REQUIREMENTS  = "requirements"
    SPECIFICATION = "specification"
    RELEASE_NOTES = "release_notes"
    DEPLOYMENT    = "deployment"
    ARCHITECTURE  = "architecture"
    MEETING       = "meeting"
    SLACK         = "slack"
    GENERAL       = "general"


# ── Filename-based rules (checked first, highest confidence) ──────────────────
_FILENAME_RULES: list[tuple[re.Pattern, DocCategory]] = [
    # Release notes / changelogs
    (re.compile(r"(CHANGELOG|CHANGES|RELEASE[\s_-]?NOTES?|HISTORY|VERSION)", re.I), DocCategory.RELEASE_NOTES),
    (re.compile(r"v?\d+\.\d+\.?\d*[\s_-]?(release|notes?)", re.I),                  DocCategory.RELEASE_NOTES),

    # Deployment / operations
    (re.compile(r"(dockerfile|docker-compose|\.github/workflows|k8s|kubernetes|terraform|helm|runbook|playbook|on[\s_-]?call)", re.I), DocCategory.DEPLOYMENT),
    (re.compile(r"\.(sh|bash|ps1)$", re.I),                                           DocCategory.DEPLOYMENT),
    (re.compile(r"(deploy|deployment|infrastructure|devops|cicd|ci[\s_-]?cd)", re.I), DocCategory.DEPLOYMENT),

    # Architecture
    (re.compile(r"(adr|architecture|system[\s_-]?design|rfc|design[\s_-]?doc)", re.I), DocCategory.ARCHITECTURE),

    # Requirements
    (re.compile(r"(prd|requirements?|user[\s_-]?stor(y|ies)|acceptance[\s_-]?criteria|backlog|epic)", re.I), DocCategory.REQUIREMENTS),

    # Specification
    (re.compile(r"(spec|specification|api[\s_-]?contract|schema|data[\s_-]?model|interface)", re.I), DocCategory.SPECIFICATION),
    (re.compile(r"\.(proto|thrift|avro|openapi|swagger)$", re.I),                                    DocCategory.SPECIFICATION),
    (re.compile(r"(openapi|swagger)", re.I),                                                          DocCategory.SPECIFICATION),

    # Meeting notes
    (re.compile(r"(meeting[\s_-]?notes?|action[\s_-]?items?|retro|retrospective|standup|sync[\s_-]?notes?)", re.I), DocCategory.MEETING),
]

# ── Content-based rules (fallback if filename is ambiguous) ───────────────────
_CONTENT_RULES: list[tuple[re.Pattern, DocCategory]] = [
    (re.compile(r"^##?\s+(release|version|changelog|v\d+\.\d+)", re.I | re.MULTILINE), DocCategory.RELEASE_NOTES),
    (re.compile(r"^##?\s+(deployment|runbook|setup|installation|getting[\s_-]?started)", re.I | re.MULTILINE), DocCategory.DEPLOYMENT),
    (re.compile(r"^##?\s+(architecture|system[\s_-]?overview|design|context)", re.I | re.MULTILINE), DocCategory.ARCHITECTURE),
    (re.compile(r"^##?\s+(requirements?|acceptance|user[\s_-]?stor)", re.I | re.MULTILINE), DocCategory.REQUIREMENTS),
    (re.compile(r"(API endpoint|curl|POST /|GET /|200 OK|openapi)", re.I), DocCategory.SPECIFICATION),
    (re.compile(r"(Attendees?:|Action items?:|Meeting date:)", re.I), DocCategory.MEETING),
]


def classify_document(filename: str, content_preview: str = "") -> str:
    """
    Classify a document into a category string.

    Args:
        filename:        The file name or relative path (e.g., "docs/CHANGELOG.md").
        content_preview: First ~500 characters of file content (optional but improves accuracy).

    Returns:
        A DocCategory string value (e.g., "release_notes").
    """
    basename = os.path.basename(filename).lower()
    full_path = filename.lower()

    # 1. Filename rules (highest priority)
    for pattern, category in _FILENAME_RULES:
        if pattern.search(basename) or pattern.search(full_path):
            return category.value

    # 2. Content rules (if filename is ambiguous)
    if content_preview:
        for pattern, category in _CONTENT_RULES:
            if pattern.search(content_preview[:2000]):
                return category.value

    return DocCategory.GENERAL.value
