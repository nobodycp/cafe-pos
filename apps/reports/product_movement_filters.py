"""فلاتر تقرير حركة الأصناف — نفس أسلوب «دفع وتتبع»."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.db.models import Q, QuerySet

SECTION_CHOICES = (
    ("all", "الكل (مبيع + راكد)"),
    ("top", "الأكثر مبيعاً فقط"),
    ("slow", "راكد فقط"),
)

TOP_SORT_CHOICES = (
    ("qty_desc", "الكمية (الأكثر)"),
    ("qty_asc", "الكمية (الأقل)"),
    ("revenue_desc", "الإيراد (الأعلى)"),
    ("profit_desc", "الربح (الأعلى)"),
)

SLOW_SORT_CHOICES = (
    ("name_asc", "الاسم (أ–ي)"),
    ("name_desc", "الاسم (ي–أ)"),
)


def _choice_or_default(raw: str, valid: set[str], default: str) -> str:
    raw = (raw or "").strip()
    return raw if raw in valid else default


def parse_movement_filters(get, *, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    date_from_s = (get.get("date_from") or "").strip()
    date_to_s = (get.get("date_to") or "").strip()
    try:
        d_from = date.fromisoformat(date_from_s) if date_from_s else today.replace(day=1)
    except ValueError:
        d_from = today.replace(day=1)
    try:
        d_to = date.fromisoformat(date_to_s) if date_to_s else today
    except ValueError:
        d_to = today
    if d_from > d_to:
        d_from, d_to = d_to, d_from

    category_raw = (get.get("category") or "").strip()
    category_id = int(category_raw) if category_raw.isdigit() else None

    product_type = (get.get("product_type") or "").strip()

    return {
        "date_from": d_from,
        "date_to": d_to,
        "date_from_iso": d_from.isoformat(),
        "date_to_iso": d_to.isoformat(),
        "q": (get.get("q") or "").strip()[:80],
        "category_id": category_id,
        "product_type": product_type,
        "section": _choice_or_default(get.get("section"), {c[0] for c in SECTION_CHOICES}, "all"),
        "sort_top": _choice_or_default(get.get("sort_top"), {c[0] for c in TOP_SORT_CHOICES}, "qty_desc"),
        "sort_slow": _choice_or_default(get.get("sort_slow"), {c[0] for c in SLOW_SORT_CHOICES}, "name_asc"),
    }


def movement_filters_open(f: dict[str, Any], *, today: date | None = None) -> bool:
    today = today or date.today()
    default_from = today.replace(day=1).isoformat()
    default_to = today.isoformat()
    return bool(
        f.get("q")
        or f.get("category_id") is not None
        or f.get("product_type")
        or f.get("section") != "all"
        or f.get("sort_top") != "qty_desc"
        or f.get("sort_slow") != "name_asc"
        or f.get("date_from_iso") != default_from
        or f.get("date_to_iso") != default_to
    )


def apply_product_name_filters(qs: QuerySet, f: dict[str, Any], *, prefix: str = "") -> QuerySet:
    """تصفية queryset منتجات أو أسطر مرتبطة بمنتج (prefix مثل product__)."""
    p = f"{prefix}" if prefix and not prefix.endswith("__") else prefix
    if f["q"]:
        qs = qs.filter(
            Q(**{f"{p}name_ar__icontains": f["q"]})
            | Q(**{f"{p}name_en__icontains": f["q"]})
            | Q(**{f"{p}barcode__icontains": f["q"]})
        )
    if f["category_id"] is not None:
        qs = qs.filter(**{f"{p}category_id": f["category_id"]})
    if f["product_type"]:
        qs = qs.filter(**{f"{p}product_type": f["product_type"]})
    return qs


def order_top_sellers(qs: QuerySet, sort_key: str) -> QuerySet:
    order_map = {
        "qty_desc": ("-total_qty", "product__name_ar"),
        "qty_asc": ("total_qty", "product__name_ar"),
        "revenue_desc": ("-total_revenue", "product__name_ar"),
        "profit_desc": ("-total_profit", "product__name_ar"),
    }
    return qs.order_by(*order_map[sort_key])


def order_slow_movers(qs: QuerySet, sort_key: str) -> QuerySet:
    if sort_key == "name_desc":
        return qs.order_by("-name_ar")
    return qs.order_by("name_ar")


def quick_period_dates(period: str, *, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    if period == "week":
        return today - timedelta(days=7), today
    if period == "year":
        return today.replace(month=1, day=1), today
    return today.replace(day=1), today
