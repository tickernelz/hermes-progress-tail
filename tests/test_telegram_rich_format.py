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
            "✅ read_file: /workspace/hermes-progress-tail/hermes_progress_tail/rendering/telegram_rich.py:1+240 · done · 0.1s",
        ]
    )

    rich = format_progress_tail_telegram_rich_markdown(content, max_table_rows=4)

    assert "## Hermes is working" in rich
    assert "### Thinking" not in rich
    assert "<details" not in rich
    assert "| Command | Result |" in rich
    assert "`python -m pytest tests/test_telegram_format_monkeypatch.py -q`" in rich
    assert "| `make verify` | ❌ failed · 12s |" in rich
    assert "| `git diff --check` | → running |" in rich
    assert "### Recent tool details" not in rich
    assert "- ✅ read_file: …/telegram_rich.py:1+240 · done · 0.1s" in rich
    assert "…/telegram_rich.py:1+240" in rich
    assert "/workspace/hermes-progress-tail" not in rich


def test_telegram_rich_reasoning_promotes_inner_titles_to_heading_blocks():
    content = "\n".join(
        [
            "**__Reasoning__**",
            "**Considering visibility options**I think the composer should remain visible.",
            "***Planning the commit message***Before committing, I am checking the diff scope.",
            "***Designing footer options****I should keep status metadata separate.*",
        ]
    )

    rich = format_progress_tail_telegram_rich_markdown(content)

    assert "## Reasoning" in rich
    assert "### Thinking" not in rich
    assert (
        "### Considering visibility options\n\nI think the composer should remain visible." in rich
    )
    assert (
        "### Planning the commit message\n\nBefore committing, I am checking the diff scope."
        in rich
    )
    assert "### Designing footer options\n\n*I should keep status metadata separate.*" in rich
    assert "**Considering visibility options**I think" not in rich
    assert "***Planning the commit message***" not in rich
    assert "***Designing footer options****I" not in rich
    assert "<details" not in rich


def test_telegram_rich_plan_renders_items_as_bullets_with_continuation_lines():
    content = "\n".join(
        [
            "**__Plan__**",
            "✓ Inspect renderer output",
            "→ **Add RED tests**for prompt cache and footer update rendering · 2 queued",
            "… 2 queued",
        ]
    )

    rich = format_progress_tail_telegram_rich_markdown(content)

    assert "## Plan" in rich
    assert "- ✓ Inspect renderer output" in rich
    assert (
        "- → **Add RED tests**\n  for prompt cache and footer update rendering · 2 queued" in rich
    )
    assert "- … 2 queued" in rich
    assert "\n→ **Add RED tests**" not in rich
    assert "**Add RED tests**for prompt" not in rich


def test_telegram_rich_reformats_embedded_progress_sections_without_code_block_wrapper():
    content = "\n".join(
        [
            "## Progress",
            "",
            "⬆️ update v0.1.80",
            "",
            "```text",
            "But raw focused content with MarkdownV2.",
            "## Reasoning",
            "### Thinking",
            "***Designing footer options****I should fix footer status.*",
            "## Tools",
            "### Recent tool details",
            "- ✅ terminal: pytest -q · done · 1.1s",
            "```",
        ]
    )

    rich = format_progress_tail_telegram_rich_markdown(content)

    assert "```text" not in rich
    assert "### Thinking" not in rich
    assert "### Recent tool details" not in rich
    assert "## Reasoning" in rich
    assert "### Designing footer options\n\n*I should fix footer status.*" in rich
    assert "## Tools" in rich
    assert "| `pytest -q` | ✅ done · 1.1s |" in rich


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
    assert "### Recent tool details" not in rich
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
    assert "### Recent tool details" not in verbose
    assert "- ✅ terminal: pytest -q · done · 1.1s" in verbose
    assert "<details" not in compact
    assert "<details" not in verbose
