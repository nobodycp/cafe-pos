"""فلاتر وبحث قائمة المنتجات — نفس منطق تقرير «دفع وتتبع» (GET + تطبيق على queryset)."""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet

from apps.catalog.models import Category, Product, Unit

PRODUCT_SORT_CHOICES = (
    ("name_asc", "الاسم (أ–ي)"),
    ("name_desc", "الاسم (ي–أ)"),
    ("price_asc", "السعر (الأقل)"),
    ("price_desc", "السعر (الأعلى)"),
    ("category", "التصنيف ثم الاسم"),
)

UNIT_SORT_CHOICES = (
    ("name_asc", "الاسم (أ–ي)"),
    ("name_desc", "الاسم (ي–أ)"),
    ("code_asc", "الرمز (أ–ي)"),
)

CATEGORY_SORT_CHOICES = (
    ("order", "الترتيب الافتراضي"),
    ("name_asc", "الاسم (أ–ي)"),
    ("name_desc", "الاسم (ي–أ)"),
)

ACTIVE_FILTER_CHOICES = (
    ("", "الكل"),
    ("1", "نشط فقط"),
    ("0", "معطّل فقط"),
)

STOCK_FILTER_CHOICES = (
    ("", "الكل"),
    ("tracked", "يتتبع مخزون"),
    ("not", "بدون تتبع مخزون"),
)

PARENT_FILTER_CHOICES = (
    ("", "كل التصنيفات"),
    ("root", "رئيسية فقط (بدون أب)"),
)


def _choice_or_default(raw: str, valid: set[str], default: str) -> str:
    raw = (raw or "").strip()
    return raw if raw in valid else default


def parse_product_filters(get) -> dict[str, Any]:
    valid_types = {c[0] for c in Product.ProductType.choices}
    product_type = (get.get("product_type") or "").strip()
    if product_type not in valid_types:
        product_type = ""

    category_raw = (get.get("category") or "").strip()
    category_id = None
    if category_raw.isdigit():
        category_id = int(category_raw)

    return {
        "q": (get.get("q") or "").strip(),
        "category_id": category_id,
        "product_type": product_type,
        "active": _choice_or_default(get.get("active"), {"", "1", "0"}, ""),
        "stock": _choice_or_default(get.get("stock"), {"", "tracked", "not"}, ""),
        "sort": _choice_or_default(
            get.get("sort"),
            {c[0] for c in PRODUCT_SORT_CHOICES},
            "name_asc",
        ),
    }


def parse_unit_filters(get) -> dict[str, Any]:
    return {
        "q": (get.get("q") or "").strip(),
        "sort": _choice_or_default(
            get.get("sort"),
            {c[0] for c in UNIT_SORT_CHOICES},
            "name_asc",
        ),
    }


def parse_category_filters(get) -> dict[str, Any]:
    parent_raw = (get.get("parent") or "").strip()
    parent: str | int = ""
    if parent_raw == "root":
        parent = "root"
    elif parent_raw.isdigit():
        parent = int(parent_raw)

    return {
        "q": (get.get("q") or "").strip(),
        "active": _choice_or_default(get.get("active"), {"", "1", "0"}, ""),
        "parent": parent,
        "sort": _choice_or_default(
            get.get("sort"),
            {c[0] for c in CATEGORY_SORT_CHOICES},
            "order",
        ),
    }


def apply_product_filters(qs: QuerySet, f: dict[str, Any]) -> QuerySet:
    if f["q"]:
        qs = qs.filter(
            Q(name_ar__icontains=f["q"])
            | Q(name_en__icontains=f["q"])
            | Q(barcode__icontains=f["q"])
        )
    if f["category_id"] is not None:
        qs = qs.filter(category_id=f["category_id"])
    if f["product_type"]:
        qs = qs.filter(product_type=f["product_type"])
    if f["active"] == "1":
        qs = qs.filter(is_active=True)
    elif f["active"] == "0":
        qs = qs.filter(is_active=False)
    if f["stock"] == "tracked":
        qs = qs.filter(is_stock_tracked=True)
    elif f["stock"] == "not":
        qs = qs.filter(is_stock_tracked=False)

    order_map = {
        "name_asc": ("name_ar",),
        "name_desc": ("-name_ar",),
        "price_asc": ("selling_price", "name_ar"),
        "price_desc": ("-selling_price", "name_ar"),
        "category": ("category__name_ar", "name_ar"),
    }
    return qs.order_by(*order_map[f["sort"]])


def apply_unit_filters(qs: QuerySet, f: dict[str, Any]) -> QuerySet:
    if f["q"]:
        qs = qs.filter(
            Q(name_ar__icontains=f["q"])
            | Q(name_en__icontains=f["q"])
            | Q(code__icontains=f["q"])
        )
    order_map = {
        "name_asc": ("name_ar",),
        "name_desc": ("-name_ar",),
        "code_asc": ("code",),
    }
    return qs.order_by(*order_map[f["sort"]])


def apply_category_filters(qs: QuerySet, f: dict[str, Any]) -> QuerySet:
    if f["q"]:
        qs = qs.filter(Q(name_ar__icontains=f["q"]) | Q(name_en__icontains=f["q"]))
    if f["active"] == "1":
        qs = qs.filter(is_active=True)
    elif f["active"] == "0":
        qs = qs.filter(is_active=False)
    if f["parent"] == "root":
        qs = qs.filter(parent__isnull=True)
    elif isinstance(f["parent"], int):
        qs = qs.filter(parent_id=f["parent"])

    order_map = {
        "order": ("sort_order", "name_ar"),
        "name_asc": ("name_ar",),
        "name_desc": ("-name_ar",),
    }
    return qs.order_by(*order_map[f["sort"]])


def products_filters_open(f: dict[str, Any]) -> bool:
    return bool(
        f.get("q")
        or f.get("category_id") is not None
        or f.get("product_type")
        or f.get("active")
        or f.get("stock")
        or f.get("sort") != "name_asc"
    )


def units_filters_open(f: dict[str, Any]) -> bool:
    return bool(f.get("q") or f.get("sort") != "name_asc")


def categories_filters_open(f: dict[str, Any]) -> bool:
    parent = f.get("parent")
    return bool(
        f.get("q")
        or f.get("active")
        or (parent not in ("", None))
        or f.get("sort") != "order"
    )


def category_filter_options() -> list[Category]:
    return list(Category.objects.filter(is_active=True).order_by("sort_order", "name_ar"))

def parent_category_options() -> list[Category]:
    return list(
        Category.objects.filter(is_active=True, parent__isnull=True)
        .order_by("sort_order", "name_ar")
    )
