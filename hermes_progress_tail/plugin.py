from __future__ import annotations

import sys

from .runtime import plugin as _plugin

sys.modules[__name__] = _plugin
