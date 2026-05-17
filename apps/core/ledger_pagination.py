"""ترقيم كشوف الحساب مع رصيد جاري صحيح لكل صفحة."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable

from django.db.models import QuerySet, Sum
from django.http import HttpRequest

from apps.core.pagination import paginate_queryset


def paginate_amount_ledger(
    request: HttpRequest,
    entries_qs: QuerySet,
    *,
    opening_balance: Decimal,
    build_row: Callable[[Any, Decimal], dict],
    **paginate_kwargs: Any,
) -> dict[str, Any]:
    """
    entries_qs: قيود مرتبة زمنياً ضمن الفترة المفلترة.
    opening_balance: رصيد ما قبل أول قيد في الفترة.
    """
    entries_qs = entries_qs.order_by("created_at", "pk")
    pag = paginate_queryset(request, entries_qs, **paginate_kwargs)
    page = pag["page_obj"]
    start_idx = page.start_index() if callable(page.start_index) else page.start_index
    prior_n = max(0, start_idx - 1) if page.object_list else 0
    prior_sum = Decimal("0")
    if prior_n:
        agg = entries_qs[:prior_n].aggregate(s=Sum("amount"))
        prior_sum = agg["s"] or Decimal("0")
    page_opening = (opening_balance + prior_sum).quantize(Decimal("0.01"))
    running = page_opening
    rows: list[dict] = []
    for entry in page:
        running = (running + entry.amount).quantize(Decimal("0.01"))
        rows.append(build_row(entry, running))
    period_sum = entries_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    closing_balance = (opening_balance + period_sum).quantize(Decimal("0.01"))
    pag["rows"] = rows
    pag["page_opening_balance"] = page_opening
    pag["closing_balance"] = closing_balance
    return pag
