from __future__ import annotations

import sys

from .rendering import renderer as _module

sys.modules[__name__] = _module
