"""Guard: every e2e module runs in exactly one CI shard.

The ovos-e2e job uses explicit per-shard file lists (balanced for the
90-minute cap, which glob-by-letter could not achieve). Explicit lists
reintroduce the risk the globbing was meant to kill — a new
``tests/test_e2e_*.py`` silently running in NO job. This test re-closes
that hole: it parses the shard lists out of the workflow and asserts they
partition the e2e modules on disk exactly (no module missing, none listed
twice, none naming a file that does not exist).
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "tests.yml"
_TESTS = _REPO / "tests"


def _shard_listed_e2e_files():
    text = _WORKFLOW.read_text()
    # every tests/test_e2e_*.py token that appears in a shard FILES= line
    listed = re.findall(r"tests/(test_e2e_[a-z0-9_]+\.py)", text)
    return listed


def test_every_e2e_module_is_in_exactly_one_shard():
    on_disk = {p.name for p in _TESTS.glob("test_e2e_*.py")}
    listed = _shard_listed_e2e_files()
    listed_set = set(listed)

    missing = on_disk - listed_set
    assert not missing, (
        f"e2e modules on disk but in NO shard (would silently not run): "
        f"{sorted(missing)}")

    duplicated = [f for f in listed_set if listed.count(f) > 1]
    assert not duplicated, f"e2e modules listed in more than one shard: {duplicated}"

    phantom = listed_set - on_disk
    assert not phantom, f"shard lists name e2e files that do not exist: {sorted(phantom)}"
