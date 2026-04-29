from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from django.db import transaction

from apps.billing.tab_service import (
    compute_order_totals,
    create_sale_invoice_core,
    next_invoice_number,
    order_payment_source,
)
from apps.core.payment_methods import payments_list_to_dict
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
    payments: List[Tuple],
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

    src = order_payment_source(order)
    payment_rows = []
    for item in payments:
        if not item:
            continue
        method = str(item[0])
        raw_amt = item[1]
        amt = raw_amt if isinstance(raw_amt, Decimal) else Decimal(str(raw_amt))
        if amt <= 0:
            continue
        pn = str(item[2]).strip()[:120] if len(item) > 2 else ""
        ph = str(item[3]).strip()[:40] if len(item) > 3 else ""
        payment_rows.append(
            {"method": method, "amount": amt, "payer_name": pn, "payer_phone": ph, "source": src}
        )
    inv = create_sale_invoice_core(
        order=order, user=user, pay_by_method=pay_map, customer=customer, payment_rows=payment_rows
    )

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
