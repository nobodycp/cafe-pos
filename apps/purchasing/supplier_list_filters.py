"""فلاتر قائمة الموردين — نفس أسلوب تقرير «دفع وتتبع» (GET + تطبيق على queryset)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.db.models import Case, DecimalField, ExpressionWrapper, F, IntegerField, Q, QuerySet, Value, When
from django.db.models.functions import Coalesce

from apps.purchasing.models import Supplier

SUPPLIER_SORT_CHOICES = (
    ("name_asc", "الاسم (أ–ي)"),
    ("name_desc", "الاسم (ي–أ)"),
    ("balance_desc", "رصيد المورد (الأعلى)"),
    ("balance_asc", "رصيد المورد (الأقل)"),
    ("net_desc", "بعد المسحوبات (الأعلى)"),
    ("net_asc", "بعد المسحوبات (الأقل)"),
)

LINKED_FILTER_CHOICES = (
    ("", "الكل"),
    ("yes", "مع عميل مرتبط"),
    ("no", "بدون عميل مرتبط"),
)

COMMISSION_FILTER_CHOICES = (
    ("", "الكل"),
    ("yes", "بائع نسبة فقط"),
    ("no", "غير بائع نسبة"),
)

NET_SIDE_CHOICES = (
    ("", "كل الأرصدة"),
    ("positive", "علينا (بعد المسحوبات > 0)"),
    ("negative", "له (بعد المسحوبات < 0)"),
    ("zero", "صفر بعد المسحوبات"),
)


def _choice_or_default(raw: str, valid: set[str], default: str) -> str:
    raw = (raw or "").strip()
    return raw if raw in valid else default


def parse_hide_zero_net(get) -> bool:
    """إخفاء من رصيدهم بعد المسحوبات = 0 — مفعّل افتراضياً عند غياب المعامل."""
    if hasattr(get, "getlist"):
        parts = get.getlist("hide_zero_net")
    else:
        raw = get.get("hide_zero_net") if hasattr(get, "get") else None
        parts = [raw] if raw is not None else []
    if not parts:
        return True
    last = (parts[-1] or "").strip().lower()
    return last not in ("0", "false", "off")


def _parse_amount(raw: str) -> Decimal | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def parse_supplier_filters(get) -> dict[str, Any]:
    return {
        "q": (get.get("q") or "").strip(),
        "linked": _choice_or_default(get.get("linked"), {"", "yes", "no"}, ""),
        "commission": _choice_or_default(get.get("commission"), {"", "yes", "no"}, ""),
        "net_side": _choice_or_default(
            get.get("net_side"),
            {"", "positive", "negative", "zero"},
            "",
        ),
        "sort": _choice_or_default(
            get.get("sort"),
            {c[0] for c in SUPPLIER_SORT_CHOICES},
            "name_asc",
        ),
        "min_balance": _parse_amount(get.get("min_balance") or ""),
        "max_balance": _parse_amount(get.get("max_balance") or ""),
        "min_net": _parse_amount(get.get("min_net") or ""),
        "max_net": _parse_amount(get.get("max_net") or ""),
        "hide_zero_net": parse_hide_zero_net(get),
        "min_balance_s": (get.get("min_balance") or "").strip(),
        "max_balance_s": (get.get("max_balance") or "").strip(),
        "min_net_s": (get.get("min_net") or "").strip(),
        "max_net_s": (get.get("max_net") or "").strip(),
    }


def supplier_list_base_queryset() -> QuerySet:
    dec0 = Value(Decimal("0"), output_field=DecimalField(max_digits=14, decimal_places=2))
    money = DecimalField(max_digits=14, decimal_places=2)
    return (
        Supplier.objects.filter(is_active=True)
        .select_related("linked_customer")
        .annotate(
            cust_balance_ann=Coalesce(F("linked_customer__balance"), dec0, output_field=money),
            net_balance_ann=ExpressionWrapper(
                F("balance") - Coalesce(F("linked_customer__balance"), dec0),
                output_field=money,
            ),
            _supplier_name_script_group=Case(
                When(name_ar__regex=r"^\s*[A-Za-z0-9]", then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
    )


def apply_supplier_filters(qs: QuerySet, f: dict[str, Any]) -> QuerySet:
    if f["q"]:
        qs = qs.filter(
            Q(name_ar__icontains=f["q"])
            | Q(name_en__icontains=f["q"])
            | Q(phone__icontains=f["q"])
            | Q(email__icontains=f["q"])
        )
    if f["linked"] == "yes":
        qs = qs.filter(linked_customer__isnull=False)
    elif f["linked"] == "no":
        qs = qs.filter(linked_customer__isnull=True)
    if f["commission"] == "yes":
        qs = qs.filter(is_commission_vendor=True)
    elif f["commission"] == "no":
        qs = qs.filter(is_commission_vendor=False)
    if f["net_side"] == "positive":
        qs = qs.filter(net_balance_ann__gt=Decimal("0"))
    elif f["net_side"] == "negative":
        qs = qs.filter(net_balance_ann__lt=Decimal("0"))
    elif f["net_side"] == "zero":
        qs = qs.filter(net_balance_ann=Decimal("0"))
    if f["min_balance"] is not None:
        qs = qs.filter(balance__gte=f["min_balance"])
    if f["max_balance"] is not None:
        qs = qs.filter(balance__lte=f["max_balance"])
    if f["min_net"] is not None:
        qs = qs.filter(net_balance_ann__gte=f["min_net"])
    if f["max_net"] is not None:
        qs = qs.filter(net_balance_ann__lte=f["max_net"])
    if f["hide_zero_net"] and not f["net_side"]:
        qs = qs.exclude(net_balance_ann=Decimal("0"))

    order_map = {
        "name_asc": ("_supplier_name_script_group", "name_ar", "pk"),
        "name_desc": ("_supplier_name_script_group", "-name_ar", "-pk"),
        "balance_desc": ("-balance", "name_ar"),
        "balance_asc": ("balance", "name_ar"),
        "net_desc": ("-net_balance_ann", "name_ar"),
        "net_asc": ("net_balance_ann", "name_ar"),
    }
    return qs.order_by(*order_map[f["sort"]])


def supplier_filters_open(f: dict[str, Any]) -> bool:
    return bool(
        f.get("q")
        or f.get("linked")
        or f.get("commission")
        or f.get("net_side")
        or f.get("min_balance") is not None
        or f.get("max_balance") is not None
        or f.get("min_net") is not None
        or f.get("max_net") is not None
        or not f.get("hide_zero_net")
        or f.get("sort") != "name_asc"
    )
