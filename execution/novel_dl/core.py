"""Core data types and base class for site adapters."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chapter:
    """A single chapter in a novel.

    ``num`` is the logical chapter number (1-based, as it appears on the site).
    ``index`` is the position in the downloader's working list (0-based) and
    is used only for deterministic file naming when ``num`` is missing.
    """

    num: int | None
    title: str
    url: str
    index: int = 0
    text: str | None = None


@dataclass
class Book:
    """Metadata about a novel plus the list of all known chapters."""

    title: str
    author: str = ""
    slug: str = ""
    source_url: str = ""
    cover_url: str = ""
    description: str = ""
    chapters: list[Chapter] = field(default_factory=list)


class UnsupportedSiteError(RuntimeError):
    """Raised when no registered adapter can handle the given URL."""


class SiteAdapter:
    """Base class for site-specific scrapers.

    Subclasses must implement :meth:`matches`, :meth:`fetch_book` and
    :meth:`fetch_chapter`. ``site_id`` is a short slug used for output paths
    and logging.

    ``cancel_event`` is an optional :class:`threading.Event`; when set,
    long-running adapters (e.g. paginated chapter-list fetches) must stop
    at the next safe boundary and raise :class:`FetchError`. This lets
    the GUI's Stop button interrupt a slow book load without waiting for
    every pagination page to finish.
    """

    site_id: str = "generic"

    @classmethod
    def matches(cls, url: str) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def fetch_book(
        self, url: str, *, cancel_event=None,
    ) -> Book:  # pragma: no cover - abstract
        raise NotImplementedError

    def fetch_chapter(self, chapter: Chapter) -> Chapter:  # pragma: no cover - abstract
        raise NotImplementedError
