import sys as _sys

if _sys.version_info < (3, 11, 4):
    raise RuntimeError(
        f"velodb-mcp-server client requires Python >= 3.11.4 "
        f"(for tarfile data filter, PEP 706). "
        f"Current: {_sys.version.split()[0]}"
    )

__version__ = "0.1.0"
