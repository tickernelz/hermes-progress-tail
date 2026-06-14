from hermes_progress_tail.monkeypatches import format_progress_tail_telegram_rich_markdown
from hermes_progress_tail.rendering.telegram_rich import (
    RichDoc,
    RichHeading,
    RichParagraph,
    RichPreformatted,
    RichTable,
    telegram_rich_message_payload,
)


def test_telegram_rich_document_renders_markdown_blocks_without_collapsible_details():
    doc = RichDoc(
        [
            RichHeading("Verification evidence", level=2),
            RichParagraph("Fresh verification that already ran:"),
            RichTable(
                headers=("Command", "Result"),
                rows=(("`pytest -q`", "✅ pass"), ("`make verify`", "❌ fail")),
            ),
            RichHeading("Raw command output", level=3),
            RichPreformatted("FAIL tests/test_demo.py", language="text"),
        ]
    )

    payload = telegram_rich_message_payload(doc)

    assert payload == {"markdown": doc.to_markdown()}
    assert "## Verification evidence" in payload["markdown"]
    assert "| Command | Result |" in payload["markdown"]
    assert "### Raw command output" in payload["markdown"]
    assert "<details" not in payload["markdown"]
    assert "```text\nFAIL tests/test_demo.py\n```" in payload["markdown"]


def test_telegram_rich_formatter_adds_verification_table_details_and_short_paths():
    content = "\n".join(
        [
            "**Hermes is working**",
            "────────────────",
            "**Now** running tests",
            "**__Reasoning__**",
            "*Checking the implementation path*",
            "**__Tools__**",
            "✅ terminal: python -m pytest tests/test_telegram_format_monkeypatch.py -q · done · 1.2s",
            "❌ terminal: make verify · failed · 12s",
            "→ terminal: git diff --check · running",
            "✅ read_file: /home/zhafron/Projects/hermes-progress-tail/hermes_progress_tail/rendering/telegram_rich.py:1+240 · done · 0.1s",
        ]
    )

    rich = format_progress_tail_telegram_rich_markdown(content, max_table_rows=4)

    assert "## Hermes is working" in rich
    assert "### Thinking" in rich
    assert "<details" not in rich
    assert "| Command | Result |" in rich
    assert "`python -m pytest tests/test_telegram_format_monkeypatch.py -q`" in rich
    assert "| `make verify` | ❌ failed · 12s |" in rich
    assert "| `git diff --check` | → running |" in rich
    assert "### Recent tool details" in rich
    assert "…/telegram_rich.py:1+240" in rich
    assert "/home/zhafron/Projects/hermes-progress-tail" not in rich


def test_telegram_rich_reasoning_keeps_inner_heading_on_its_own_line():
    content = "\n".join(
        [
            "**__Reasoning__**",
            "**Considering visibility options**I think the composer should remain visible.",
        ]
    )

    rich = format_progress_tail_telegram_rich_markdown(content)

    assert "### Thinking" in rich
    assert "**Considering visibility options**\nI think the composer should remain visible." in rich
    assert "**Considering visibility options**I think" not in rich
    assert "<details" not in rich


def test_telegram_rich_formatter_builds_status_table_and_failure_first_tools():
    content = "\n".join(
        [
            "**Hermes is working**",
            "────────────────",
            "**Now** running tests",
            "**Why** verifying implementation",
            "**State** 4 tools · 3 done · 1 failed",
            "**Time** just now",
            "**__Tools__**",
            "✅ terminal: pytest -q · done · 1.1s",
            "❌ terminal: ruff check . · failed · 0.2s",
            "✅ terminal: compileall -q . · done · 0.4s",
            "✅ terminal: git diff --check · done · 0.1s",
        ]
    )

    rich = format_progress_tail_telegram_rich_markdown(
        content,
        max_table_rows=8,
        compact_success=True,
        max_detail_items=2,
    )

    assert "| Field | Value |" in rich
    assert "| Now | running tests |" in rich
    assert "| Why | verifying implementation |" in rich
    assert "## Failed tools" in rich
    assert rich.index("## Failed tools") < rich.index("## Verification evidence")
    assert "| `ruff check .` | ❌ failed · 0.2s |" in rich
    assert "### Recent tool details" in rich
    assert "- ✅ terminal: pytest -q · done · 1.1s" in rich
    assert "- ❌ terminal: ruff check . · failed · 0.2s" in rich
    assert "2 more tool events" in rich


def test_telegram_rich_formatter_compacts_success_details_by_default_but_can_show_visible_details():
    content = "\n".join(
        [
            "**__Tools__**",
            "✅ terminal: pytest -q · done · 1.1s",
            "✅ terminal: ruff check . · done · 0.2s",
        ]
    )

    compact = format_progress_tail_telegram_rich_markdown(content, compact_success=True)
    verbose = format_progress_tail_telegram_rich_markdown(content, compact_success=False)

    assert "| `pytest -q` | ✅ done · 1.1s |" in compact
    assert "### Recent tool details" not in compact
    assert "### Recent tool details" in verbose
    assert "<details" not in compact
    assert "<details" not in verbose
