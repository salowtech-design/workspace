"""Search hook. Each module registers a searcher; results are filtered by permission.

A searcher is (user, query, limit) -> iterable of (resource_type, obj, title, url).
Searchers MUST pre-filter to what the user may see inside their own query (for example a
membership join) so inaccessible rows never crowd out permitted ones. The hook then runs
can(user, "view", ...) on every result as a backstop and drops anything refused, and it
returns only id, title and url, so no module can leak object content through search.
"""

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from apps.core.foundation.permissions import can

Searcher = Callable[[Any, str, int], Iterable[tuple[str, Any, str, str]]]
_SEARCHERS: dict[str, Searcher] = {}


@dataclass(frozen=True)
class SearchHit:
    resource_type: str
    object_id: str
    title: str
    url: str


def register_searcher(name: str, searcher: Searcher) -> None:
    if name in _SEARCHERS and _SEARCHERS[name] is not searcher:
        raise ValueError(f"searcher {name!r} already registered")
    _SEARCHERS[name] = searcher


def search(user: Any, query: str, *, types: Optional[Iterable[str]] = None, limit: int = 20):
    query = (query or "").strip()
    if len(query) < 2:
        return []
    wanted = set(types) if types else None
    hits: list[SearchHit] = []
    for name, fn in sorted(_SEARCHERS.items()):
        if wanted and name not in wanted:
            continue
        for rtype, obj, title, url in fn(user, query, limit * 3):
            if can(user, "view", rtype, obj):
                hits.append(SearchHit(rtype, str(getattr(obj, "pk", "")), title, url))
                if len(hits) >= limit:
                    return hits
    return hits


def search_grouped(
    user: Any, query: str, *, types: Optional[Iterable[str]] = None, per_module: int = 20
):
    """Fair allocation: each searcher gets its own cap, so one module cannot starve another.

    Returns {searcher_name: (hits, truncated)} in name order. truncated is True when that module
    had more permitted matches than per_module (the extras are not shown). The same can() backstop
    runs on every hit."""
    query = (query or "").strip()
    out: dict[str, tuple[list[SearchHit], bool]] = {}
    if len(query) < 2:
        return out
    wanted = set(types) if types else None
    for name, fn in sorted(_SEARCHERS.items()):
        if wanted and name not in wanted:
            continue
        hits: list[SearchHit] = []
        for rtype, obj, title, url in fn(user, query, (per_module + 1) * 3):
            if can(user, "view", rtype, obj):
                hits.append(SearchHit(rtype, str(getattr(obj, "pk", "")), title, url))
                if len(hits) > per_module:
                    break
        out[name] = (hits[:per_module], len(hits) > per_module)
    return out
