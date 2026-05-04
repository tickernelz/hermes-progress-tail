from __future__ import annotations

import sys

from .gateway import compat as _module

sys.modules[__name__] = _module
