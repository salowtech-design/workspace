"""Search hook. Each module registers a searcher; results are filtered by permission.

A searcher is (user, query, limit) -> iterable of (resource_type, obj, title, url).
Searchers MUST pre-filter to what the user may see inside their own query (for example a
membership join) so inaccessible rows never crowd out permitted ones. The hook then runs
can(user, "view", ...) on every result as a backstop and drops anything refused, and it
returns only id, title and url, so no module can leak object content through search.
"""

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from django.db.models import Case, IntegerField, Value, When

from apps.core.foundation.permissions import can

Searcher = Callable[[Any, str, int], Iterable[tuple[str, Any, str, str]]]
_SEARCHERS: dict[str, Searcher] = {}


def relevance(name: str, needle: str, *, also_exact: str = ""):
    """Ordering expression for a searcher query: 0 the whole text equals the search, 1 it starts with it,
    2 it starts a later word, 3 it is somewhere inside. name is an Upper() annotation, needle is upper case.
    Sorting by this BEFORE the row cap keeps the best matches from being cut by newest-first or id order.
    also_exact is a second annotation (an ID) whose exact match also counts as 0."""
    first = When(**{name: needle}, then=Value(0))
    whens = [When(**{also_exact: needle}, then=Value(0))] if also_exact else []
    return Case(
        first, *whens,
        When(**{name + "__startswith": needle}, then=Value(1)),
        When(**{name + "__contains": " " + needle}, then=Value(2)),
        default=Value(3), output_field=IntegerField(),
    )  # fmt: skip


def rank_text(text: str, query: str) -> int:
    """The same four classes in Python, for merging several queries inside one searcher."""
    t, q = (text or "").upper(), query.upper()
    if t == q:
        return 0
    if t.startswith(q):
        return 1
    return 2 if (" " + q) in t else 3


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
        pool = (per_module + 1) * 3
        raw = 0
        broke = False
        for rtype, obj, title, url in fn(user, query, pool):
            raw += 1
            if can(user, "view", rtype, obj):
                hits.append(SearchHit(rtype, str(getattr(obj, "pk", "")), title, url))
                if len(hits) > per_module:
                    broke = True
                    break
        # A refused hit can leave fewer than per_module shown while the pool was full: say there may be more.
        out[name] = (hits[:per_module], broke or raw >= pool)
    return out
