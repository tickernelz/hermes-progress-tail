from __future__ import annotations

import sys

from .utils import redaction as _module

sys.modules[__name__] = _module
