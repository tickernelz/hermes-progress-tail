from scripts.check_session_context_inventory import scan_sources


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
