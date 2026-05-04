from __future__ import annotations

import sys

from .rendering import delegate as _module

sys.modules[__name__] = _module
