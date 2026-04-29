"""استعلامات بحث العملاء المشتركة بين الواجهات (POS، الصندوق، …)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q

if TYPE_CHECKING:
    from django.db.models import QuerySet

from apps.contacts.models import Customer


def active_customers_search_qs(q: str, *, limit: int = 20) -> "QuerySet[Customer]":
    """
    عملاء نشطون، اختياري تصفية بالاسم العربي/الإنجليزي أو الهاتف.
    `q` يُقصّ تلقائياً لطول آمن للاستعلام.
    """
    q = (q or "").strip()[:80]
    qs: QuerySet[Customer] = Customer.objects.filter(is_active=True)
    if q:
        qs = qs.filter(
            Q(name_ar__icontains=q) | Q(phone__icontains=q) | Q(name_en__icontains=q)
        )
    return qs.order_by("name_ar")[:limit]
