"""Resolve the currently live platform adapter for a session context.

Hermes core rebuilds a platform adapter in place when its transport dies
(``gateway.adapters[platform] = adapter``). A reference captured when the
context was registered then points at a stopped object and every later edit
fails, so the progress card freezes. Resolution therefore happens at use time.

The lookup is deliberately total: it never returns ``None`` and never raises,
because it sits directly on the rendering path.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _lookup(adapters: Any, key: Any) -> Any:
    if key is None or key == "":
        return None
    try:
        return adapters.get(key)
    except Exception:
        return None


def _lookup_by_value(adapters: Any, platform: Any) -> Any:
    """Match an enum-shaped key by its ``value`` when only the string is known."""

    name = str(getattr(platform, "value", platform) or "")
    if not name:
        return None
    try:
        for key, value in adapters.items():
            if str(getattr(key, "value", key) or "") == name:
                return value
    except Exception:
        return None
    return None


def resolve_live_adapter(gateway: Any, platform: Any, fallback: Any) -> Any:
    """Return the gateway's current adapter for ``platform``, else ``fallback``.

    ``gateway.adapters`` is enum-keyed in Hermes core and string-keyed in much
    of the test suite, so the adapter's own ``platform`` attribute is tried
    first, the context's platform string second, and an enum ``value`` match
    last.
    """

    if gateway is None:
        return fallback
    try:
        adapters = getattr(gateway, "adapters", None)
        if not adapters:
            return fallback
        live = _lookup(adapters, getattr(fallback, "platform", None))
        if live is None:
            live = _lookup(adapters, platform)
        if live is None:
            live = _lookup_by_value(adapters, platform)
        return fallback if live is None else live
    except Exception as exc:
        logger.debug("hermes-progress-tail adapter resolution failed: %s", exc)
        return fallback
