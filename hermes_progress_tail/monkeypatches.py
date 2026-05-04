from __future__ import annotations

import sys

from .hooks import monkeypatches as _module

sys.modules[__name__] = _module
