"""Search hook. Each module registers a searcher; results are filtered by permission.

A searcher is (query, limit) -> iterable of (resource_type, obj, title, url). The hook
drops every result the user may not view, so no module can leak through search.
"""

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from apps.core.foundation.permissions import can

Searcher = Callable[[str, int], Iterable[tuple[str, Any, str, str]]]
_SEARCHERS: dict[str, Searcher] = {}


@dataclass(frozen=True)
class SearchHit:
    resource_type: str
    object_id: str
    title: str
    url: str


def register_searcher(name: str, searcher: Searcher) -> None:
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
        for rtype, obj, title, url in fn(query, limit * 3):
            if can(user, "view", rtype, obj):
                hits.append(SearchHit(rtype, str(getattr(obj, "pk", "")), title, url))
                if len(hits) >= limit:
                    return hits
    return hits
