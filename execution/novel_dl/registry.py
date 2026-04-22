"""Adapter registry: resolve a URL to the correct :class:`SiteAdapter`."""

from __future__ import annotations

from .core import SiteAdapter, UnsupportedSiteError

_ADAPTERS: list[type[SiteAdapter]] = []


def register_adapter(cls: type[SiteAdapter]) -> type[SiteAdapter]:
    """Decorator / function: add ``cls`` to the adapter registry."""
    if cls not in _ADAPTERS:
        _ADAPTERS.append(cls)
    return cls


def get_adapter(url: str) -> SiteAdapter:
    """Return an instance of the first adapter that claims ``url``."""
    _ensure_builtins_loaded()
    for cls in _ADAPTERS:
        try:
            if cls.matches(url):
                return cls()
        except Exception:
            continue
    raise UnsupportedSiteError(
        f"No adapter found for URL: {url}\n"
        "Supported sites: ranobes.net, ranobes.com, freewebnovel.com"
    )


def list_adapters() -> list[type[SiteAdapter]]:
    """Return all registered adapter classes (for diagnostics / tests)."""
    _ensure_builtins_loaded()
    return list(_ADAPTERS)


def _ensure_builtins_loaded() -> None:
    """Import the bundled adapters so they self-register on first use."""
    # Importing the package triggers adapter module imports, which call
    # ``register_adapter`` on their classes.
    from . import adapters  # noqa: F401
