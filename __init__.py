try:
    from .hermes_progress_tail import plugin as _plugin
except ImportError:
    from hermes_progress_tail import plugin as _plugin

register = _plugin.register
_get_renderer = _plugin._get_renderer
_on_pre_gateway_dispatch = _plugin._on_pre_gateway_dispatch
_on_pre_tool_call = _plugin._on_pre_tool_call
_on_post_tool_call = _plugin._on_post_tool_call
_on_post_llm_call = _plugin._on_post_llm_call
_on_session_reset = _plugin._on_session_reset
_on_session_finalize = _plugin._on_session_finalize

__all__ = [
    "register",
    "_get_renderer",
    "_on_pre_gateway_dispatch",
    "_on_pre_tool_call",
    "_on_post_tool_call",
    "_on_post_llm_call",
    "_on_session_reset",
    "_on_session_finalize",
]
