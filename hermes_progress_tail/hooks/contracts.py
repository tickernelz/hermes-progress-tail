from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ReasoningDeltaCallback = Callable[..., object]
AssistantProgressCallback = Callable[..., bool]
DelegateProgressCallback = Callable[..., object]
CompressionStatusCallback = Callable[..., bool]
CompressionLifecycleCallback = Callable[..., object]
AdapterContextCallback = Callable[..., object]
GatewayStopCallback = Callable[..., object]
ReasoningEnabledCallback = Callable[[Any], bool]
TelegramSettingsCallback = Callable[[], Any | None]


@dataclass(frozen=True)
class HookCallbacks:
    on_reasoning_delta: ReasoningDeltaCallback
    on_assistant_progress: AssistantProgressCallback
    on_delegate_progress: DelegateProgressCallback
    on_compression_status: CompressionStatusCallback
    on_compression_lifecycle: CompressionLifecycleCallback
    register_adapter_context: AdapterContextCallback
    on_gateway_stop: GatewayStopCallback
    reasoning_enabled: ReasoningEnabledCallback
    telegram_settings: TelegramSettingsCallback


def inert_hook_callbacks() -> HookCallbacks:
    def on_reasoning_delta(agent: Any, text: str, *, source: str = "provider") -> object:
        return None

    def on_assistant_progress(agent: Any, text: str, *, already_streamed: bool = False) -> bool:
        return False

    def on_delegate_progress(
        parent_agent: Any,
        event_type: Any,
        tool_name: Any = None,
        preview: Any = None,
        cb_args: Any = None,
        **event_kwargs: Any,
    ) -> object:
        return None

    def on_compression_status(agent: Any, text: str) -> bool:
        return False

    def on_compression_lifecycle(
        agent: Any, *, phase: Any, old_session_id: Any, **metrics: Any
    ) -> object:
        return None

    def register_adapter_context(adapter: Any, event: Any) -> object:
        return None

    def on_gateway_stop(runner: Any, *, session_key: Any, source: Any) -> object:
        return None

    def reasoning_enabled(agent: Any) -> bool:
        return False

    def telegram_settings() -> Any | None:
        return None

    return HookCallbacks(
        on_reasoning_delta=on_reasoning_delta,
        on_assistant_progress=on_assistant_progress,
        on_delegate_progress=on_delegate_progress,
        on_compression_status=on_compression_status,
        on_compression_lifecycle=on_compression_lifecycle,
        register_adapter_context=register_adapter_context,
        on_gateway_stop=on_gateway_stop,
        reasoning_enabled=reasoning_enabled,
        telegram_settings=telegram_settings,
    )
