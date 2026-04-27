"""جلسات الطاولات: فتح واستئناف."""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from django.db import transaction

from apps.billing.tab_service import compute_order_totals, sum_tab_payments
from apps.contacts.models import Customer
from apps.core.models import log_audit
from apps.core.services import SessionService
from apps.pos.models import DiningTable, Order, TableSession


def _d(v):
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


@transaction.atomic
def open_or_resume_table_session(
    *,
    user,
    dining_table: DiningTable,
    customer: Optional[Customer] = None,
    guest_label: str = "",
) -> Tuple[TableSession, Order]:
    """جلسة مفتوحة لكل طاولة في الوردية؛ طلب واحد مفتوح حتى التسوية."""
    ws = SessionService.require_open_session()
    ts = (
        TableSession.objects.select_for_update()
        .filter(
            work_session=ws,
            dining_table=dining_table,
            status=TableSession.Status.OPEN,
        )
        .first()
    )
    if ts:
        if customer and not ts.customer_id:
            ts.customer = customer
            ts.save(update_fields=["customer", "updated_at"])
        elif customer and ts.customer_id != customer.pk:
            pass
        order = (
            Order.objects.select_for_update()
            .filter(table_session=ts, status=Order.Status.OPEN)
            .order_by("-id")
            .first()
        )
        if not order:
            order = Order.objects.create(
                work_session=ws,
                table_session=ts,
                order_type=Order.OrderType.DINE_IN,
                table=dining_table,
                customer=customer or ts.customer,
            )
            log_audit(user, "pos.order.create", "pos.Order", order.pk, {"table_session": ts.pk})
        else:
            if order.is_held:
                order.is_held = False
                order.save(update_fields=["is_held", "updated_at"])
                log_audit(user, "pos.order.resume", "pos.Order", order.pk, {})
            if customer and not order.customer_id:
                order.customer = customer
                order.save(update_fields=["customer", "updated_at"])
        return ts, order

    ts = TableSession.objects.create(
        work_session=ws,
        dining_table=dining_table,
        customer=customer,
        guest_label=(guest_label or "")[:160],
    )
    order = Order.objects.create(
        work_session=ws,
        table_session=ts,
        order_type=Order.OrderType.DINE_IN,
        table=dining_table,
        customer=customer,
    )
    log_audit(user, "pos.table.open", "pos.TableSession", ts.pk, {"table": dining_table.pk})
    log_audit(user, "pos.order.create", "pos.Order", order.pk, {"table_session": ts.pk})
    return ts, order


def table_session_money_totals(table_session: TableSession) -> dict:
    """إجمالي الفاتورة والمدفوع والمتبقي لكل الطلبات المفتوحة على الجلسة."""
    grand = Decimal("0")
    paid = Decimal("0")
    for o in Order.objects.filter(table_session=table_session, status=Order.Status.OPEN):
        t = compute_order_totals(o)
        grand += t["grand"]
        paid += sum_tab_payments(o)
    remaining = (grand - paid).quantize(Decimal("0.01"))
    if remaining < 0:
        remaining = Decimal("0")
    return {"grand": grand, "paid": paid, "remaining": remaining}


def floor_rows_for_session(work_session) -> List[dict]:
    """صفوف لعرض شبكة الطاولات — محسّنة بحد أدنى من الاستعلامات."""
    from django.db.models import Sum, F, Q, DecimalField
    from django.db.models.functions import Coalesce

    tables = list(
        DiningTable.objects.filter(is_active=True, is_cancelled=False).order_by("sort_order", "name_ar")
    )

    open_sessions = {
        ts.dining_table_id: ts
        for ts in TableSession.objects.filter(
            work_session=work_session,
            status=TableSession.Status.OPEN,
        ).select_related("customer")
    }

    if open_sessions:
        session_ids = [s.pk for s in open_sessions.values()]
        order_totals = {}
        for row in Order.objects.filter(
            table_session_id__in=session_ids,
            status=Order.Status.OPEN,
        ).values("table_session_id").annotate(
            total_grand=Coalesce(
                Sum(F("lines__quantity") * F("lines__unit_price"), output_field=DecimalField()),
                Decimal("0"),
            ),
            total_paid=Coalesce(Sum("tab_payments__amount"), Decimal("0")),
        ):
            order_totals[row["table_session_id"]] = {
                "grand": (row["total_grand"] or Decimal("0")).quantize(Decimal("0.01")),
                "paid": (row["total_paid"] or Decimal("0")).quantize(Decimal("0.01")),
            }
    else:
        order_totals = {}

    rows = []
    for t in tables:
        ts = open_sessions.get(t.pk)
        if not ts:
            rows.append({
                "table": t,
                "session": None,
                "status": "free",
                "grand": Decimal("0"),
                "paid": Decimal("0"),
                "remaining": Decimal("0"),
                "customer_label": "",
            })
            continue
        m = order_totals.get(ts.pk, {"grand": Decimal("0"), "paid": Decimal("0")})
        grand = m["grand"]
        paid = m["paid"]
        remaining = max(grand - paid, Decimal("0")).quantize(Decimal("0.01"))
        cust = ""
        if ts.customer_id:
            cust = ts.customer.name_ar
        elif ts.guest_label:
            cust = ts.guest_label
        st = "occupied"
        if grand == 0:
            st = "open_empty"
        elif paid > 0 and remaining > Decimal("0"):
            st = "partial"
        rows.append({
            "table": t,
            "session": ts,
            "status": st,
            "grand": grand,
            "paid": paid,
            "remaining": remaining,
            "customer_label": cust,
        })
    return rows


