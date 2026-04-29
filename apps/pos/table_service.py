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


@transaction.atomic
def auto_close_empty_dine_in_tab_orders(work_session) -> int:
    """
    طلبات صالة مفتوحة بلا أسطر وبلا دفعات تاب وبمجموع صفر — تُكمّل CHECKED_OUT وتُغلق جلسة الطاولة إن لزم.
    يمنع بقاء طاولة «فارغة مفتوحة» ويمنع إغلاق الوردية بسبب طلب فارغ.
    """
    from django.db.models import Count

    from apps.billing.models import SaleInvoice
    from apps.billing.tab_service import (
        _close_table_session_if_no_open_orders,
        compute_order_totals,
        sum_tab_payments,
    )

    qs = (
        Order.objects.filter(
            work_session=work_session,
            status=Order.Status.OPEN,
            order_type=Order.OrderType.DINE_IN,
        )
        .annotate(_lc=Count("lines", distinct=True))
        .filter(_lc=0)
        .select_for_update()
    )
    closed = 0
    for order in qs:
        if SaleInvoice.objects.filter(order=order).exists():
            continue
        if sum_tab_payments(order) > Decimal("0.005"):
            continue
        if compute_order_totals(order)["grand"] > Decimal("0.005"):
            continue
        sid = order.table_session_id
        order.status = Order.Status.CHECKED_OUT
        order.save(update_fields=["status", "updated_at"])
        _close_table_session_if_no_open_orders(table_session_id=sid)
        log_audit(
            None,
            "pos.order.auto_close_empty_tab",
            "pos.Order",
            str(order.pk),
            {"work_session": work_session.pk},
        )
        closed += 1
    return closed


@transaction.atomic
def close_stale_open_table_sessions_for_work_session(work_session) -> int:
    """TableSession مفتوحة على الوردية ولا يوجد عليها أي Order بحالة OPEN."""
    from django.utils import timezone

    now = timezone.now()
    closed = 0
    candidates = list(
        TableSession.objects.select_for_update().filter(
            work_session=work_session,
            status=TableSession.Status.OPEN,
        )
    )
    for ts in candidates:
        if Order.objects.filter(table_session=ts, status=Order.Status.OPEN).exists():
            continue
        ts.status = TableSession.Status.CLOSED
        ts.closed_at = now
        ts.save(update_fields=["status", "closed_at", "updated_at"])
        log_audit(None, "pos.table_session.auto_close_stale", "pos.TableSession", str(ts.pk), {})
        closed += 1
    return closed


def repair_stale_table_sessions_for_floor(work_session) -> None:
    """خريطة الطاولات فقط: إغلاق جلسات OPEN بلا أي طلب OPEN — دون لمس طلبات فارغة نشطة."""
    close_stale_open_table_sessions_for_work_session(work_session)


def prepare_work_session_for_shift_close(work_session) -> None:
    """قبل محاولة إغلاق الوردية: طلبات صالة فارغة عالقة ثم جلسات يتيمة. لا تُستدعى من شاشة الكاشير العادية."""
    auto_close_empty_dine_in_tab_orders(work_session)
    close_stale_open_table_sessions_for_work_session(work_session)


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
    """صفوف لعرض شبكة الطاولات.

    لا نستخدم annotate على Order مع lines وtab_payments معاً — ينتج ضرباً كارتيزياً
    فيُحسب كل دفعة بعدد أسطر الطلب (مثلاً 10 شيكل × 3 أسطر = 30).
    """
    repair_stale_table_sessions_for_floor(work_session)

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
        order_totals = {ts.pk: table_session_money_totals(ts) for ts in open_sessions.values()}
    else:
        order_totals = {}

    rows = []
    for t in tables:
        ts = open_sessions.get(t.pk)
        if not ts:
            # طلب صالة مفتوح على الطاولة من دون جلسة طاولة مفتوحة (جلسة أُغلقت والطلب بقي، أو بيانات ناقصة)
            orphan_qs = Order.objects.filter(
                work_session=work_session,
                status=Order.Status.OPEN,
                table=t,
                order_type=Order.OrderType.DINE_IN,
            ).select_related("customer")
            if not orphan_qs.exists():
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
            grand = Decimal("0")
            paid = Decimal("0")
            cust = ""
            for o in orphan_qs:
                tr = compute_order_totals(o)
                grand += tr["grand"]
                paid += sum_tab_payments(o)
                if o.customer_id and not cust and o.customer:
                    cust = o.customer.name_ar
            remaining = max(grand - paid, Decimal("0")).quantize(Decimal("0.01"))
            st = "occupied"
            if grand == 0:
                st = "open_empty"
            elif paid > 0 and remaining > Decimal("0"):
                st = "partial"
            rows.append({
                "table": t,
                "session": None,
                "status": st,
                "grand": grand,
                "paid": paid,
                "remaining": remaining,
                "customer_label": cust,
            })
            continue
        m = order_totals.get(
            ts.pk,
            {"grand": Decimal("0"), "paid": Decimal("0"), "remaining": Decimal("0")},
        )
        grand = m["grand"]
        paid = m["paid"]
        remaining = m.get("remaining", max(grand - paid, Decimal("0")).quantize(Decimal("0.01")))
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


