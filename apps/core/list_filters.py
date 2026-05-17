"""أدوات مشتركة لبحث وفلاتر قوائم الغلاف (GET)."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Sequence

from django.http import HttpRequest


def get_search_q(request: HttpRequest, *, param: str = "q") -> str:
    return (request.GET.get(param) or "").strip()


def parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def parse_date_range(request: HttpRequest, *, from_param: str = "date_from", to_param: str = "date_to") -> tuple[date | None, date | None]:
    d_from = parse_iso_date(request.GET.get(from_param))
    d_to = parse_iso_date(request.GET.get(to_param))
    if d_from and d_to and d_from > d_to:
        d_from, d_to = d_to, d_from
    return d_from, d_to


def filters_open(request: HttpRequest, keys: Sequence[str], *, exclude: Iterable[str] = ("page", "per_page")) -> bool:
    ex = set(exclude)
    for key in keys:
        if key in ex:
            continue
        val = (request.GET.get(key) or "").strip()
        if val:
            return True
    return False


def iso_date_str(d: date | None) -> str:
    return d.isoformat() if d else ""
