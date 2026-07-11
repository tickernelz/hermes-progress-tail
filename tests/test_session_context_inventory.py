from pathlib import Path

from scripts.check_session_context_inventory import render_inventory, scan_repository, scan_sources

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests/fixtures/session_context_constructors.json"


def test_constructor_inventory_distinguishes_all_call_forms_and_helper():
    records = scan_sources(
        {
            "probe.py": """\
from package.models.state import SessionContext
from package.models.state import SessionContext as Alias
import package.models.state as state
Assigned = Alias

def helper():
    SessionContext()
    state.SessionContext(value=1)
    Alias(1)
    Assigned(flag=True)
"""
        }
    )

    assert {call["callee"]: call["kind"] for call in records["calls"]} == {
        "SessionContext": "direct",
        "state.SessionContext": "qualified",
        "Alias": "import_alias",
        "Assigned": "assignment_alias",
    }
    assert [(alias["name"], alias["kind"]) for alias in records["aliases"]] == [
        ("Alias", "import"),
        ("Assigned", "assignment"),
    ]
    assert records["helpers"] == [{"path": "probe.py", "line": 6, "name": "helper"}]


def test_checked_in_inventory_matches_tracked_repository_sources():
    assert FIXTURE.read_text() == render_inventory(scan_repository(ROOT))
