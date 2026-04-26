"""
Routes incoming messages to the correct handler.
Uses fuzzy keyword matching so Siri voice dictation doesn't need exact phrases.
Project matching is by name only — aliases have been removed.
"""

import re
import difflib
import logging
from dataclasses import dataclass
from enum import Enum, auto

logger = logging.getLogger(__name__)


class Intent(Enum):
    SWITCH_PROJECT = auto()
    LIST_PROJECTS = auto()
    CURRENT_STATUS = auto()
    ASK_CLAUDE = auto()


@dataclass
class RouteResult:
    intent: Intent
    project_name: str | None = None  # for SWITCH_PROJECT
    ambiguous_matches: list[str] | None = None  # when fuzzy match is ambiguous


# Keyword patterns for intent detection (order matters — checked top-down)
_SWITCH_PATTERNS = re.compile(
    r"\b(switch|change|go|move|use|open|load|set)\b.{0,30}\b(project|dir|directory|to)\b"
    r"|\b(switch|change|go|move|use|open|load|set) to\b"
    r"|\buse\s+\w",  # "use web", "use api", etc.
    re.IGNORECASE,
)

_LIST_PATTERNS = re.compile(
    r"\b(list|show|what|which|display)\b.{0,20}\bproject",
    re.IGNORECASE,
)

_STATUS_PATTERNS = re.compile(
    r"\b(where am i|current project|what project am i|which project am i|active project)\b",
    re.IGNORECASE,
)

# Threshold for fuzzy project name matching
FUZZY_THRESHOLD = 0.55
AMBIGUITY_THRESHOLD = 0.15  # if top two matches are within this delta, it's ambiguous


def detect_intent(text: str, projects: list[dict]) -> RouteResult:
    """Detect the intent of the message and return a RouteResult."""
    text_lower = text.lower().strip()

    if _STATUS_PATTERNS.search(text_lower):
        return RouteResult(intent=Intent.CURRENT_STATUS)

    if _LIST_PATTERNS.search(text_lower):
        return RouteResult(intent=Intent.LIST_PROJECTS)

    if _SWITCH_PATTERNS.search(text_lower):
        match = _find_project(text_lower, projects)
        return match

    return RouteResult(intent=Intent.ASK_CLAUDE)


def _find_project(text: str, projects: list[dict]) -> RouteResult:
    """Fuzzy-match a project name from the message text."""
    candidates: list[tuple[str, str]] = [(p["name"], p["name"]) for p in projects]

    scored: list[tuple[float, str]] = []
    for candidate_text, project_name in candidates:
        score = difflib.SequenceMatcher(None, text, candidate_text).ratio()
        # Also try matching just the last word or two of the text (handles "switch to website")
        words = text.split()
        for n in (1, 2, 3):
            if len(words) >= n:
                tail = " ".join(words[-n:])
                tail_score = difflib.SequenceMatcher(None, tail, candidate_text).ratio()
                score = max(score, tail_score)
        scored.append((score, project_name))

    # Sort descending by score, deduplicate project names (keep best score)
    seen: dict[str, float] = {}
    for score, name in scored:
        if name not in seen or score > seen[name]:
            seen[name] = score

    ranked = sorted(seen.items(), key=lambda x: x[1], reverse=True)
    logger.debug("Project match scores: %s", ranked[:5])

    if not ranked or ranked[0][1] < FUZZY_THRESHOLD:
        # No good match — fall back to asking Claude
        return RouteResult(intent=Intent.ASK_CLAUDE)

    top_name, top_score = ranked[0]

    # Check for ambiguity
    if len(ranked) > 1:
        second_name, second_score = ranked[1]
        if top_score - second_score < AMBIGUITY_THRESHOLD and second_score >= FUZZY_THRESHOLD:
            return RouteResult(
                intent=Intent.SWITCH_PROJECT,
                project_name=top_name,
                ambiguous_matches=[top_name, second_name],
            )

    return RouteResult(intent=Intent.SWITCH_PROJECT, project_name=top_name)
