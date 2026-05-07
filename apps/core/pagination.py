"""ترقيم صفحات موحّد لقوائم الغلاف (GET: page, per_page)."""

from __future__ import annotations

from typing import Any, Iterable, Sequence, Tuple, Union

from django.core.paginator import Paginator
from django.http import HttpRequest

# قيم افتراضية لمعظم جداول الغلاف
DEFAULT_PER_PAGE_CHOICES: Tuple[int, ...] = (25, 50, 100)


def paginate_queryset(
    request: HttpRequest,
    queryset: Union[Any, Iterable],
    *,
    default_per_page: int = 25,
    per_page_choices: Sequence[int] | None = None,
) -> dict:
    """
    يعيد page_obj و per_page و pagination_query (بدون مفتاح page) للحفاظ على باقي معاملات البحث.
    """
    choices: Tuple[int, ...] = tuple(per_page_choices) if per_page_choices is not None else DEFAULT_PER_PAGE_CHOICES
    try:
        per_page = int(request.GET.get("per_page", default_per_page))
    except (TypeError, ValueError):
        per_page = default_per_page
    if per_page not in choices:
        per_page = default_per_page if default_per_page in choices else choices[0]

    page = Paginator(queryset, per_page).get_page(request.GET.get("page"))
    q = request.GET.copy()
    q.pop("page", None)
    return {
        "page_obj": page,
        "per_page": per_page,
        "per_page_choices": choices,
        "pagination_query": q.urlencode(),
    }
