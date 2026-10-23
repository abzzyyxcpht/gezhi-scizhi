"""Release identity shared by the desktop, self-check and packaging tools."""
from __future__ import annotations

APP_VERSION = '0.13.0'
VERSION = APP_VERSION
WINDOWS_VERSION = tuple(int(part) for part in APP_VERSION.split('.')) + (0,)
