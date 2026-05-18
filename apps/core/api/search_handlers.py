"""نتائج بحث مشتركة — نفس منطق pos/purchasing/contacts."""

from __future__ import annotations

from django.db.models import Count, Q

from apps.accounting.models import Account
from apps.catalog.models import Category, Product, Unit
from apps.contacts.customer_lookup import active_customers_search_qs, customer_search_result_row
from apps.purchasing.models import Supplier


def search_customers(q: str) -> list:
    return [customer_search_result_row(c) for c in active_customers_search_qs(q, limit=20)]


def search_sale_products(q: str) -> list:
    """منتجات البيع (نفس pos:products_search)."""
    q = (q or "").strip()[:80]
    if len(q) < 1:
        return []
    qs = (
        Product.objects.filter(is_active=True)
        .exclude(product_type=Product.ProductType.RAW)
        .filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(barcode__icontains=q))
        .select_related("category")
        .annotate(modifier_group_count=Count("modifier_groups"))
        .order_by("name_ar")[:15]
    )
    return [
        {
            "id": p.pk,
            "name_ar": p.name_ar,
            "price": str(p.selling_price),
            "category": p.category.name_ar if p.category else "",
            "has_modifiers": p.modifier_group_count > 0,
        }
        for p in qs
    ]


def search_categories(q: str) -> list:
    q = (q or "").strip()
    if len(q) < 1:
        return []
    rows = Category.objects.filter(is_active=True, name_ar__icontains=q).order_by("name_ar")[:24]
    return [{"id": c.pk, "name_ar": c.name_ar} for c in rows]


def search_units(q: str) -> list:
    q = (q or "").strip()
    if len(q) < 1:
        return []
    rows = Unit.objects.filter(name_ar__icontains=q).order_by("name_ar")[:30]
    return [{"id": u.pk, "name_ar": u.name_ar, "code": u.code} for u in rows]


def search_accounts(q: str) -> list:
    q = (q or "").strip()
    if len(q) < 1:
        return []
    qs = (
        Account.objects.filter(is_active=True)
        .filter(Q(code__icontains=q) | Q(name_ar__icontains=q) | Q(name_en__icontains=q))
        .order_by("code")[:30]
    )
    return [
        {
            "id": a.pk,
            "code": a.code,
            "name_ar": a.name_ar,
            "account_type": a.account_type,
        }
        for a in qs
    ]


def search_suppliers(q: str) -> list:
    q = (q or "").strip()
    if len(q) < 1:
        return []
    qs = (
        Supplier.objects.filter(is_active=True)
        .filter(name_ar__icontains=q)
        .order_by("name_ar")[:30]
    )
    return [{"id": s.pk, "name_ar": s.name_ar, "phone": s.phone} for s in qs]
