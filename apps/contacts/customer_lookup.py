"""استعلامات بحث العملاء المشتركة بين الواجهات (POS، الصندوق، …)."""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

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


def customer_balance_search_fields(balance: Decimal | None) -> dict[str, str]:
    """حقول الرصيد لنتائج البحث (موجب = عليه، سالب = له)."""
    bal = (balance if balance is not None else Decimal("0")).quantize(Decimal("0.01"))
    if bal > 0:
        return {"balance": str(bal), "balance_hint": "عليه", "balance_kind": "debit"}
    if bal < 0:
        return {"balance": str(bal), "balance_hint": "له", "balance_kind": "credit"}
    return {"balance": str(bal), "balance_hint": "متوازن", "balance_kind": "zero"}


def customer_search_result_row(customer: Customer) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": customer.pk,
        "name_ar": customer.name_ar,
        "phone": customer.phone or "",
    }
    row.update(customer_balance_search_fields(customer.balance))
    return row
