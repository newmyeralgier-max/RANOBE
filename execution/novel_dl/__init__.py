"""Universal ranobe/webnovel downloader with pluggable site adapters."""

from .core import Book, Chapter, SiteAdapter, UnsupportedSiteError
from .registry import get_adapter, register_adapter

__all__ = [
    "Book",
    "Chapter",
    "SiteAdapter",
    "UnsupportedSiteError",
    "get_adapter",
    "register_adapter",
]
