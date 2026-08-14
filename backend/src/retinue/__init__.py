"""Retinue - your AI retinue.

Self-hosted, single-process, multi-provider AI chat platform with versioned
custom agents. `pip install retinue && retinue serve`.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("retinue")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
