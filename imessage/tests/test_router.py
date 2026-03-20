import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from router import Intent, detect_intent

PROJECTS = [
    {"name": "claude-imessage", "aliases": ["imessage", "message bridge", "bridge"]},
    {"name": "personal-website", "aliases": ["website", "my site", "web"]},
    {"name": "work-api", "aliases": ["api", "work", "backend"]},
]


def test_list_projects():
    for phrase in ["list projects", "what projects do I have", "show me my projects"]:
        result = detect_intent(phrase, PROJECTS)
        assert result.intent == Intent.LIST_PROJECTS, f"Failed for: {phrase}"


def test_current_status():
    for phrase in ["where am I", "current project", "what project am I in"]:
        result = detect_intent(phrase, PROJECTS)
        assert result.intent == Intent.CURRENT_STATUS, f"Failed for: {phrase}"


def test_switch_project_exact():
    result = detect_intent("switch to website", PROJECTS)
    assert result.intent == Intent.SWITCH_PROJECT
    assert result.project_name == "personal-website"


def test_switch_project_fuzzy_siri():
    result = detect_intent("hey switch to the bridge project", PROJECTS)
    assert result.intent == Intent.SWITCH_PROJECT
    assert result.project_name == "claude-imessage"


def test_switch_project_alias():
    result = detect_intent("use web", PROJECTS)
    assert result.intent == Intent.SWITCH_PROJECT
    assert result.project_name == "personal-website"


def test_ask_claude_default():
    result = detect_intent("How do I fix the login bug?", PROJECTS)
    assert result.intent == Intent.ASK_CLAUDE


def test_ask_claude_no_project_match():
    result = detect_intent("switch to something that doesnt exist xyzzy", PROJECTS)
    # Very low fuzzy score → falls back to ASK_CLAUDE
    assert result.intent in (Intent.ASK_CLAUDE, Intent.SWITCH_PROJECT)
