"""The skill-ready fixtures fail under CI and skip only locally (#79)."""
from types import SimpleNamespace

import pytest

from tests.conftest import skill_not_ready, wait_for_skill_intents

NO_SKILL = SimpleNamespace(bus=SimpleNamespace(ee=SimpleNamespace(_events={})))


def _fixture_outcome():
    """Run the fixture pattern against an agent whose skill never loads.

    Returns the outcome exception, so a skip cannot pass as a skipped test.
    """
    assert wait_for_skill_intents(NO_SKILL, "skill.absent", timeout=0) is False
    try:
        skill_not_ready("skill.absent registered no intent handler")
    except (pytest.fail.Exception, pytest.skip.Exception) as outcome:
        return outcome
    raise AssertionError("skill_not_ready returned")


def test_ci_fails(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("HARNESS_ALLOW_SKILL_SKIP", raising=False)
    outcome = _fixture_outcome()
    assert isinstance(outcome, pytest.fail.Exception)
    assert "skill.absent" in str(outcome)


def test_ci_with_flag_skips(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("HARNESS_ALLOW_SKILL_SKIP", "1")
    assert isinstance(_fixture_outcome(), pytest.skip.Exception)


def test_local_skips(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    assert isinstance(_fixture_outcome(), pytest.skip.Exception)
