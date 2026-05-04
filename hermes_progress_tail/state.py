from __future__ import annotations

import sys

from .models import state as _module

sys.modules[__name__] = _module
