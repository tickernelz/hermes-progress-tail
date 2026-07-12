from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..hooks.contracts import HookCallbacks
from ..hooks.install_report import PatchInstallReport
from ..rendering.renderer import ProgressRenderer
from ..settings.types import Settings
from . import agent_events, context
from .config_runtime import _load_runtime_settings
from .environment import _agent_session_id, _agent_session_key
from .origin import _should_suppress_agent_progress


def _capture_state() -> dict[str, Any]:
    return {
        "status": "never",
        "session_id": "",
        "session_key_present": False,
        "text_preview": "",
        "already_streamed": False,
        "updated_at": 0.0,
    }


@dataclass
class PluginRuntime:
    settings_loader: Callable[[], Settings] = _load_runtime_settings
    renderer_factory: Callable[[Settings], ProgressRenderer] = ProgressRenderer
    renderer: ProgressRenderer | None = None
    assistant_capture: dict[str, Any] = field(default_factory=_capture_state)
    patch_report: PatchInstallReport = field(default_factory=PatchInstallReport)

    def get_renderer(self) -> ProgressRenderer:
        settings = self.settings_loader()
        if self.renderer is None:
            self.renderer = self.renderer_factory(settings)
        else:
            self.renderer.replace_settings(settings)
        return self.renderer

    def replace_settings(self, settings: Settings) -> None:
        if self.renderer is None:
            self.renderer = self.renderer_factory(settings)
        else:
            self.renderer.replace_settings(settings)

    def callbacks(self) -> HookCallbacks:
        return HookCallbacks(
            on_reasoning_delta=agent_events.on_reasoning_delta_from_agent,
            on_assistant_progress=agent_events.on_assistant_progress_from_agent,
            on_delegate_progress=agent_events.on_delegate_progress_from_agent,
            on_compression_status=agent_events.on_compression_status_from_agent,
            on_compression_lifecycle=agent_events.on_compression_lifecycle_from_agent,
            register_adapter_context=context.register_context_from_adapter_event,
            on_gateway_stop=agent_events.on_gateway_stop_from_runner,
            reasoning_enabled=self.reasoning_enabled,
            telegram_settings=self.telegram_settings,
        )

    def set_patch_report(self, report: PatchInstallReport) -> None:
        self.patch_report = report

    def reasoning_enabled(self, agent: Any) -> bool:
        if _should_suppress_agent_progress(agent):
            return False
        renderer = self.get_renderer()
        session = renderer.find_context(_agent_session_id(agent), _agent_session_key(agent))
        return bool(
            session is not None
            and session.reasoning_enabled
            and renderer.settings.reasoning.enabled
        )

    def telegram_settings(self) -> Any | None:
        return self.get_renderer().settings.telegram
