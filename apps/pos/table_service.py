"""جلسات الطاولات: فتح واستئناف."""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from typing import List, Optional, Tuple

from django.db import transaction

from apps.billing.tab_service import compute_order_totals, sum_tab_payments
from apps.contacts.models import Customer
from apps.core.models import log_audit
from apps.core.services import SessionService
from apps.pos.models import DiningTable, Order, TableSession


def _ws_filter(work_session):
    if work_session is None:
        return {"work_session__isnull": True}
    return {"work_session": work_session}


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
            **_ws_filter(ws),
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
            **_ws_filter(work_session),
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
            {"work_session": work_session.pk if work_session else None},
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
            **_ws_filter(work_session),
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
        retire_ephemeral_dining_table_if_safe(dining_table_id=ts.dining_table_id)
        closed += 1
    return closed


def repair_stale_table_sessions_for_floor(work_session) -> None:
    """خريطة الطاولات فقط: إغلاق جلسات OPEN بلا أي طلب OPEN — دون لمس طلبات فارغة نشطة."""
    close_stale_open_table_sessions_for_work_session(work_session)


def prepare_work_session_for_shift_close(work_session) -> None:
    """قبل محاولة إغلاق الوردية: طلبات صالة فارغة عالقة ثم جلسات يتيمة. لا تُستدعى من شاشة الكاشير العادية."""
    auto_close_empty_dine_in_tab_orders(work_session)
    close_stale_open_table_sessions_for_work_session(work_session)


def retire_ephemeral_dining_table_if_safe(*, dining_table_id: int) -> None:
    """
    بعد إغلاق آخر جلسة مفتوحة على طاولة «مؤقتة» (كاشير): إلغاء الطاولة ناعماً.
    لا تُمس الطاولات غير المؤقتة (إعدادات ثابتة).
    """
    table = DiningTable.objects.filter(pk=dining_table_id, is_cancelled=False).first()
    if not table or not table.ephemeral:
        return
    if TableSession.objects.filter(dining_table_id=dining_table_id, status=TableSession.Status.OPEN).exists():
        return
    if Order.objects.filter(table_id=dining_table_id, status=Order.Status.OPEN).exists():
        return
    table.soft_cancel(reason="إغلاق جلسة طاولة من الكاشير")
    log_audit(None, "pos.table.retire_ephemeral", "pos.DiningTable", str(table.pk), {"name_ar": table.name_ar})


def table_tile_label(table: DiningTable) -> str:
    """رقم قصير للعرض على مربع الطاولة (آخر رقم في الاسم أو أول رقم يُعثر عليه)."""
    s = (table.name_ar or "").strip()
    if not s:
        return "?"
    m = re.search(r"(\d+)\s*$", s)
    if m:
        return m.group(1)
    m2 = re.search(r"(\d+)", s)
    if m2:
        return m2.group(1)
    return s[:2]


def table_tile_color(*, status: str, grand: Decimal, paid: Decimal, remaining: Decimal) -> str:
    """
    ألوان مربع الطاولة في الكاشير:
    green — فارغة | yellow — مفتوحة بلا رصيد مستحق | red — بها طلب ومتبقي | blue — دفعة جزئية.
    """
    if status == "free":
        return "green"
    if status == "open_empty":
        return "yellow"
    if status == "partial":
        return "blue"
    if status == "occupied":
        if grand > Decimal("0.005") and remaining > Decimal("0.005"):
            return "red"
        return "yellow"
    return "yellow"


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
    """صفوف لعرض طاولات الوردية: جلسات مفتوحة فقط (أسماء كاملة) + طلبات يتيمة نادرة بلا جلسة."""
    repair_stale_table_sessions_for_floor(work_session)

    open_sessions_list = list(
        TableSession.objects.filter(
            **_ws_filter(work_session),
            status=TableSession.Status.OPEN,
        )
        .select_related("dining_table", "customer")
        .order_by("dining_table__name_ar")
    )
    open_sessions_list = [ts for ts in open_sessions_list if not ts.dining_table.is_cancelled]

    order_totals = {ts.pk: table_session_money_totals(ts) for ts in open_sessions_list}

    rows = []
    open_table_ids = {ts.dining_table_id for ts in open_sessions_list}
    open_ts_ids = {ts.pk for ts in open_sessions_list}

    for ts in open_sessions_list:
        t = ts.dining_table
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
        lbl = (t.name_ar or "").strip() or "طاولة"
        rows.append({
            "table": t,
            "session": ts,
            "status": st,
            "grand": grand,
            "paid": paid,
            "remaining": remaining,
            "customer_label": cust,
            "tile_label": lbl,
            "tile_color": table_tile_color(status=st, grand=grand, paid=paid, remaining=remaining),
        })

    orphan_orders = (
        Order.objects.filter(
            **_ws_filter(work_session),
            status=Order.Status.OPEN,
            order_type=Order.OrderType.DINE_IN,
            table_id__isnull=False,
        )
        .exclude(table_session_id__in=open_ts_ids)
        .select_related("table", "customer")
    )
    by_table: dict[int, list] = defaultdict(list)
    for o in orphan_orders:
        if o.table_id and o.table_id not in open_table_ids and o.table and not o.table.is_cancelled:
            by_table[o.table_id].append(o)

    for tid, olist in by_table.items():
        t = olist[0].table
        grand = Decimal("0")
        paid = Decimal("0")
        cust = ""
        for o in olist:
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
        lbl = (t.name_ar or "").strip() or "طاولة"
        rows.append({
            "table": t,
            "session": None,
            "status": st,
            "grand": grand,
            "paid": paid,
            "remaining": remaining,
            "customer_label": cust,
            "tile_label": lbl,
            "tile_color": table_tile_color(status=st, grand=grand, paid=paid, remaining=remaining),
        })

    rows.sort(key=lambda r: (r["table"].name_ar or "").strip())
    return rows


