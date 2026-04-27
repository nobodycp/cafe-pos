from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from django.db import transaction

from apps.billing.tab_service import (
    compute_order_totals,
    create_sale_invoice_core,
    next_invoice_number,
    payments_list_to_dict,
)
from apps.contacts.models import Customer
from apps.core.models import log_audit
from apps.core.services import SessionService
from apps.billing.models import SaleInvoice
from apps.pos.models import Order, TableSession


@transaction.atomic
def checkout_order(
    *,
    order: Order,
    user,
    payments: List[Tuple[str, Decimal]],
    customer: Optional[Customer] = None,
) -> SaleInvoice:
    """
    دفع كامل في خطوة واحدة (سفري/بدون تاب). مجموع الدفعات = الإجمالي شامل الضريبة والخدمة.
    """
    session = SessionService.require_open_session()
    if order.work_session_id != session.id:
        raise ValueError("ORDER_SESSION_MISMATCH")
    if order.status != Order.Status.OPEN:
        raise ValueError("ORDER_NOT_OPEN")
    if not order.lines.exists():
        raise ValueError("ORDER_EMPTY")
    if order.tab_payments.filter(sale_invoice__isnull=True).exists():
        raise ValueError("USE_TAB_PAYMENT_FLOW")

    totals = compute_order_totals(order)
    pay_map = payments_list_to_dict(payments)
    pay_sum = sum(pay_map.values(), Decimal("0")).quantize(Decimal("0.01"))
    if pay_sum != totals["grand"]:
        raise ValueError("PAYMENT_SUM_MISMATCH")

    inv = create_sale_invoice_core(order=order, user=user, pay_by_method=pay_map, customer=customer)

    order.status = Order.Status.CHECKED_OUT
    order.save(update_fields=["status", "updated_at"])

    if order.table_session_id and order.table_session.status == TableSession.Status.OPEN:
        ts = order.table_session
        ts.status = TableSession.Status.CLOSED
        from django.utils import timezone

        ts.closed_at = timezone.now()
        ts.save(update_fields=["status", "closed_at", "updated_at"])

    log_audit(user, "sale.checkout", "billing.SaleInvoice", inv.pk, {"total": str(inv.total)})
    return inv


__all__ = ["checkout_order", "next_invoice_number", "compute_order_totals", "create_sale_invoice_core"]
