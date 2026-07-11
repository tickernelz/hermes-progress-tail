from collections import deque

from hermes_progress_tail.models.state import SessionContext
from tests.test_assistant_progress import (
    test_assistant_progress_can_be_disabled_per_platform as _assistant_feature_gate,
)
from tests.test_event_reducer import (
    test_architecture_accepts_exact_feature_disable_contract as _all_feature_gates,
)
from tests.test_hooks import (
    test_session_context_positional_strategy_compatibility as _positional_strategy,
)
from tests.test_hooks import (
    test_telegram_dm_metadata_omits_direct_topic_for_general_topic as _general_topic_metadata,
)
from tests.test_hooks import (
    test_telegram_dm_metadata_supports_topic_without_reply_anchor as _topic_metadata,
)
from tests.test_renderer_delivery import (
    test_tool_tail_adds_compact_event_timestamp as _timestamp_rendering,
)
from tests.test_renderer_part2 import (
    test_focused_header_uses_renderer_agent_label_when_configured as _agent_label,
)
from tests.test_renderer_part7 import (
    test_delegate_progress_can_be_disabled_per_platform as _delegate_feature_gate,
)
from tests.test_session_registry import (
    test_architecture_auto_strategy_and_find as _auto_strategy,
)
from tests.test_session_registry import (
    test_characterization_source_message_fence as _source_fence,
)
from tests.test_session_state_compatibility import (
    test_all_keyword_required_construction_remains_legal as _all_keyword,
)
from tests.test_session_state_compatibility import (
    test_legacy_positional_and_keyword_construction as _legacy_construction,
)


def make_context(*, thread_id=None, **kwargs):
    return SessionContext("s", "k", "telegram", "c", thread_id, None, None, **kwargs)


def test_legacy_routing_values_and_defaults():
    ctx = make_context(
        strategy="snapshot",
        lines=7,
        preview_length=55,
        edit_interval=2.5,
        tools_enabled=False,
        assistant_enabled=False,
        reasoning_enabled=False,
        delegates_enabled=False,
        background_jobs_enabled=False,
        timestamp=True,
        timestamp_format="%S",
        agent_label="Hermes",
        chat_type="dm",
        source_message_id="42",
    )
    assert (ctx.strategy, ctx.lines, ctx.preview_length, ctx.edit_interval) == (
        "snapshot",
        7,
        55,
        2.5,
    )
    assert not any(
        (
            ctx.tools_enabled,
            ctx.assistant_enabled,
            ctx.reasoning_enabled,
            ctx.delegates_enabled,
            ctx.background_jobs_enabled,
        )
    )
    assert (ctx.timestamp, ctx.timestamp_format, ctx.agent_label) == (True, "%S", "Hermes")
    assert (ctx.chat_type, ctx.source_message_id) == ("dm", "42")
    default = make_context()
    assert (default.strategy, default.lines, default.preview_length, default.edit_interval) == (
        "auto",
        3,
        120,
        1.5,
    )
    assert all(
        (
            default.tools_enabled,
            default.assistant_enabled,
            default.reasoning_enabled,
            default.delegates_enabled,
            default.background_jobs_enabled,
        )
    )
    assert default.timestamp is None
    assert (default.timestamp_format, default.agent_label, default.chat_type) == ("", "", "")
    assert default.source_message_id is None


def test_positional_keyword_and_flat_assignment_compatibility():
    _positional_strategy()
    _legacy_construction()
    _all_keyword()
    ctx = make_context()
    replacement = {
        "strategy": "live_tail",
        "lines": 9,
        "preview_length": 88,
        "edit_interval": 3.0,
        "tools_enabled": False,
        "assistant_enabled": False,
        "reasoning_enabled": False,
        "delegates_enabled": False,
        "background_jobs_enabled": False,
        "timestamp": False,
        "timestamp_format": "%M",
        "agent_label": "Agent",
        "chat_type": "dm",
        "source_message_id": "99",
    }
    for name, value in replacement.items():
        setattr(ctx, name, value)
        assert getattr(ctx, name) == value


def test_metadata_and_resize_exact_compatibility():
    assert make_context().metadata is None
    _general_topic_metadata()
    _topic_metadata()
    reply = make_context(thread_id="77436", chat_type="dm", source_message_id="9001")
    assert reply.metadata == {
        "thread_id": "77436",
        "telegram_dm_topic_reply_fallback": True,
        "direct_messages_topic_id": "77436",
        "telegram_reply_to_message_id": "9001",
    }
    reply.tool_lines = deque(["a", "b", "c"], maxlen=8)
    reply.resize(2)
    assert list(reply.tool_lines) == ["b", "c"]
    assert reply.tool_lines.maxlen == reply.lines == 2


def test_feature_timestamp_label_strategy_and_source_behaviors():
    _all_feature_gates()
    _assistant_feature_gate()
    _delegate_feature_gate()
    _timestamp_rendering()
    _agent_label()
    _source_fence()
    _auto_strategy()
