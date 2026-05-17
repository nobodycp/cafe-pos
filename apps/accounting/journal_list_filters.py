from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet
from django.http import HttpRequest

from apps.core.list_filters import filters_open, get_search_q, parse_date_range


def parse_journal_filters(request: HttpRequest) -> dict[str, Any]:
    q = get_search_q(request)
    date_from, date_to = parse_date_range(request)
    status = (request.GET.get("status") or "").strip().lower()
    if status not in ("", "active", "reversed"):
        status = ""
    return {"q": q, "date_from": date_from, "date_to": date_to, "status": status}


def apply_journal_filters(qs: QuerySet, f: dict[str, Any]) -> QuerySet:
    if f["q"]:
        qs = qs.filter(
            Q(description__icontains=f["q"])
            | Q(entry_number__icontains=f["q"])
            | Q(reference_type__icontains=f["q"])
        )
    if f["date_from"]:
        qs = qs.filter(date__gte=f["date_from"])
    if f["date_to"]:
        qs = qs.filter(date__lte=f["date_to"])
    if f["status"] == "active":
        qs = qs.filter(is_reversed=False)
    elif f["status"] == "reversed":
        qs = qs.filter(is_reversed=True)
    return qs


def journal_filters_open(f: dict[str, Any]) -> bool:
    return bool(f["q"] or f["date_from"] or f["date_to"] or f["status"])


def journal_filter_keys() -> tuple[str, ...]:
    return ("q", "date_from", "date_to", "status", "per_page")
