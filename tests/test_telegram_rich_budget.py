import re

from hermes_progress_tail.hooks.telegram import _telegram_rich_markdown
from hermes_progress_tail.rendering.telegram_rich import (
    format_progress_tail_telegram_rich_markdown,
)
from hermes_progress_tail.rendering.telegram_rich_budget import (
    DEFAULT_MIN_NARRATIVE_CHARS,
    DEFAULT_RICH_BUDGET_BYTES,
    enforce_rich_budget,
    markdown_byte_length,
)

BUDGET = DEFAULT_RICH_BUDGET_BYTES


def _words(prefix: str, target_chars: int) -> str:
    tokens: list[str] = []
    size = 0
    index = 0
    while size < target_chars:
        token = f"{prefix}{index}"
        tokens.append(token)
        size += len(token) + 1
        index += 1
    return " ".join(tokens)


def _render(source: str) -> str:
    return format_progress_tail_telegram_rich_markdown(
        source,
        max_table_rows=8,
        verification_table=True,
        thinking_blocks=True,
        compact_success=True,
        max_detail_items=8,
        collapse_narrative_chars=1200,
    )


def _oversized_card(progress_chars: int = 4000, reasoning_chars: int = 14000) -> str:
    return "\n".join(
        [
            "**Working the budget guard**",
            "**Now** rendering an oversized card",
            "**Why** the client collapses past 8 KiB",
            "**State** running",
            "",
            "## Progress",
            _words("progress-token-", progress_chars),
            "",
            "## Reasoning",
            _words("reasoning-token-", reasoning_chars),
            "",
            "## Plan",
            "- step one",
            "- step two",
            "",
            "## Tools",
            "- ✅ terminal: pytest -q · done",
            "- ❌ terminal: mypy . · failed",
            "",
            "## Status",
            "Every section must stay visible.",
        ]
    )


SMALL_CARD = "\n".join(
    [
        "**Small card**",
        "**Now** doing a small thing",
        "",
        "## Progress",
        "Short progress note.",
        "",
        "## Reasoning",
        "A brief reasoning paragraph well inside the budget.",
    ]
)


def test_oversized_rich_card_fits_the_byte_budget():
    guarded = _telegram_rich_markdown(_oversized_card())
    assert markdown_byte_length(guarded) <= BUDGET


def test_lower_sections_survive_the_shrink():
    """The whole point: Plan/Tools/Status must stay visible, not be cut away."""
    guarded = _telegram_rich_markdown(_oversized_card())
    assert markdown_byte_length(guarded) <= BUDGET
    assert "step two" in guarded
    assert "mypy" in guarded
    assert "Every section must stay visible." in guarded


def test_negative_control_unguarded_render_exceeds_the_budget():
    """Without the guard the same card blows the budget, so the test bites."""
    unguarded = _render(_oversized_card())
    assert markdown_byte_length(unguarded) > BUDGET


def test_small_card_passes_through_byte_identical():
    assert _telegram_rich_markdown(SMALL_CARD) == _render(SMALL_CARD)


def test_shrink_is_tail_anchored_and_reasoning_degrades_first():
    progress = _words("progress-token-", 4000)
    reasoning = _words("reasoning-token-", 14000)
    card = "\n".join(
        [
            "## Progress",
            progress,
            "",
            "## Reasoning",
            reasoning,
        ]
    )
    guarded = _telegram_rich_markdown(card)
    assert markdown_byte_length(guarded) <= BUDGET
    # Newest tail survives, oldest head is dropped.
    assert reasoning.split()[-1] in guarded
    assert reasoning.split()[0] not in guarded
    # Progress degrades last: it is untouched while Reasoning still has slack.
    assert progress.split()[0] in guarded
    assert progress.split()[-1] in guarded


def test_progress_shrinks_only_after_reasoning_hits_the_floor():
    """Reasoning is driven to the floor before Progress gives up any ground."""
    progress = _words("progress-token-", 20000)
    reasoning = _words("reasoning-token-", 20000)
    card = "\n".join(
        [
            "## Progress",
            progress,
            "",
            "## Reasoning",
            reasoning,
        ]
    )
    guarded = _telegram_rich_markdown(card)
    assert markdown_byte_length(guarded) <= BUDGET
    assert "... " in guarded

    kept_progress = len(re.findall(r"progress-token-\d+", guarded))
    kept_reasoning = len(re.findall(r"reasoning-token-\d+", guarded))
    # Reasoning is parked at the floor; Progress keeps far more of its content.
    assert kept_reasoning > 0
    assert kept_progress > kept_reasoning * 5
    # The floor is a request, not a hard minimum: word-boundary trimming can
    # retain slightly fewer characters than DEFAULT_MIN_NARRATIVE_CHARS.
    reasoning_chars = kept_reasoning * len("reasoning-token-000")
    assert reasoning_chars <= DEFAULT_MIN_NARRATIVE_CHARS


def test_last_resort_truncation_never_violates_the_cap():
    """A payload with no narrative sections still gets capped."""
    bulk = "\n".join(f"Line {index}: {_words('filler-', 200)}" for index in range(200))
    card = f"**Header**\n\n## Announcements\n{bulk}"
    guarded = enforce_rich_budget(
        card,
        _render,
        budget_bytes=BUDGET,
        min_narrative_chars=DEFAULT_MIN_NARRATIVE_CHARS,
    )
    assert markdown_byte_length(_render(card)) > BUDGET
    assert markdown_byte_length(guarded) <= BUDGET


def test_multibyte_content_is_measured_in_bytes_not_characters():
    body = "🌊" * 6000
    card = f"## Progress\nnow\n\n## Reasoning\n{body}"
    guarded = enforce_rich_budget(card, _render, budget_bytes=BUDGET)
    assert markdown_byte_length(guarded) <= BUDGET


def test_non_positive_budget_disables_the_guard():
    card = _oversized_card()
    assert enforce_rich_budget(card, _render, budget_bytes=0) == _render(card)


def test_invalid_budget_setting_falls_back_to_the_default():
    guarded = enforce_rich_budget(
        _oversized_card(),
        _render,
        budget_bytes="not-a-number",  # type: ignore[arg-type]
    )
    assert markdown_byte_length(guarded) <= BUDGET


class _SendResult:
    def __init__(self, success: bool = True, message_id: str = "1", **kwargs: object) -> None:
        self.success = success
        self.message_id = message_id
        self.error = kwargs.get("error")


class _CapturingBot:
    """Stands in for the PTB bot on the edit path."""

    def __init__(self) -> None:
        self.transmitted: str | None = None

    async def do_api_request(self, method: str, api_kwargs: dict) -> None:
        self.transmitted = api_kwargs["rich_message"]["markdown"]


class _CapturingAdapter:
    """Stands in for the Hermes Telegram adapter on the send path."""

    def __init__(self) -> None:
        self._hermes_progress_tail_rich_messages = True
        self._bot = _CapturingBot()
        self.transmitted: str | None = None

    async def _try_send_rich(
        self, chat_id: str, markdown: str, reply_to: object, metadata: object
    ) -> _SendResult:
        self.transmitted = markdown
        return _SendResult()


def test_send_path_transmits_within_budget():
    """The bytes actually handed to the adapter respect the cap."""
    import asyncio

    from hermes_progress_tail.hooks import telegram as telegram_hooks

    adapter = _CapturingAdapter()
    asyncio.run(telegram_hooks._try_send_rich_message(adapter, "1", _oversized_card(), _SendResult))

    assert adapter.transmitted is not None
    assert markdown_byte_length(adapter.transmitted) <= BUDGET
    # The unguarded render of the same card really is over budget.
    assert markdown_byte_length(_render(_oversized_card())) > BUDGET


def test_edit_path_transmits_within_budget():
    """The edit payload is capped too, not just the send payload."""
    import asyncio

    from hermes_progress_tail.hooks import telegram as telegram_hooks

    adapter = _CapturingAdapter()
    asyncio.run(
        telegram_hooks._try_edit_rich_message(adapter, "1", "2", _oversized_card(), _SendResult)
    )

    assert adapter._bot.transmitted is not None
    assert markdown_byte_length(adapter._bot.transmitted) <= BUDGET
