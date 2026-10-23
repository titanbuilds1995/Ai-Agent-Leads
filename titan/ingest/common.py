"""Field-access helpers shared by the source mappers."""
from __future__ import annotations

from typing import Any, Iterable


def g(d: Any, *paths: str) -> Any:
    """First non-empty value among dotted paths (list indexes allowed: 'a.0.b')."""
    for p in paths:
        node = d
        ok = True
        for part in p.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                ok = False
                break
        if ok and node not in (None, "", [], {}):
            return node
    return None


def first_str(*vals: Any) -> str | None:
    for v in vals:
        if v not in (None, "", [], {}):
            return str(v).strip()
    return None


def price_history_from(items: Iterable[Any], price_key: tuple[str, ...], event_key: tuple[str, ...],
                       date_key: tuple[str, ...]) -> list[dict[str, Any]]:
    out = []
    for h in items or []:
        if not isinstance(h, dict):
            continue
        out.append({"date": g(h, *date_key), "price": g(h, *price_key), "event": g(h, *event_key)})
    return out
