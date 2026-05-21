"""تقرير الصناديق: وارد/صادر لكل طريقة دفع عبر فترة — مطابق لمنطق مطابقة الوردية + سندات قبض الخزينة."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from django.db.models import Q, Sum

from apps.billing.models import InvoicePayment, OrderPayment
from apps.core.models import AuditLog, PaymentMethod, WorkSession
from apps.core.operation_mode import uses_shifts
from apps.core.payment_channel_balance import continuous_opening_balance, get_opening_balance
from apps.core.payment_methods import load_payment_method_rows, payment_method_label_map
from apps.core.treasury_services import TREASURY_VOUCHER_AUDIT_ACTION
from apps.expenses.models import Expense
from apps.purchasing.models import SupplierPayment
from apps.reports.payment_channel_ledger import _as_decimal, _parse_voucher_date

QTY = Decimal("0.01")

# أقدم تاريخ لحركات «كل الوقت» في لقطة الكاشير (لا يُستخدم لفلتر الافتتاحي المستمر).
CUMULATIVE_MOVEMENTS_FROM = date(2000, 1, 1)


def _reconcile_method_q_expense(code: str) -> Q:
    if code == "cash":
        return Q(payment_method="cash") | Q(payment_method="") | Q(payment_method__isnull=True)
    return Q(payment_method=code)


def _reconcile_method_q_supplier(code: str) -> Q:
    if code == "cash":
        return Q(method="cash") | Q(method="")
    return Q(method=code)


def _opening_for_session(ws: WorkSession, code: str) -> Decimal:
    opening_json = ws.opening_balances_json or {}
    if not opening_json and ws.opening_cash is not None:
        opening_json = {"cash": str((ws.opening_cash or Decimal("0")).quantize(QTY))}
    raw = opening_json.get(code)
    if raw is None or raw == "":
        if code == "cash" and ws.opening_cash is not None:
            return (ws.opening_cash or Decimal("0")).quantize(QTY)
        return Decimal("0")
    try:
        return Decimal(str(raw)).quantize(QTY)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def opening_balances_at_period_start(date_from: date, date_to: date) -> Dict[str, Decimal]:
    """أرصدة افتتاحية من أول وردية تُفتح ضمن الفترة (إن وُجدت)."""
    ws = (
        WorkSession.objects.filter(
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        .order_by("created_at", "pk")
        .first()
    )
    if not ws:
        return {}
    out: Dict[str, Decimal] = {}
    for r in load_payment_method_rows():
        if r["ledger"] not in ("cash", "bank"):
            continue
        code = r["code"]
        out[code] = _opening_for_session(ws, code)
    return out


def _invoice_inflows_by_method(date_from: date, date_to: date) -> Dict[str, Decimal]:
    sums: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    qs = (
        InvoicePayment.objects.filter(
            invoice__is_cancelled=False,
            invoice__created_at__date__gte=date_from,
            invoice__created_at__date__lte=date_to,
        )
        .values("method")
        .annotate(s=Sum("amount"))
    )
    for row in qs:
        code = row["method"] or "cash"
        sums[code] = (row["s"] or Decimal("0")).quantize(QTY)
    return dict(sums)


def _tab_inflows_by_method(date_from: date, date_to: date) -> Dict[str, Decimal]:
    """دفعات تاب/طاولة لم تُسوَّ إلى فاتورة بعد — نقد فعلي في الصندوق."""
    sums: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in (
        OrderPayment.objects.filter(
            sale_invoice__isnull=True,
            order__is_cancelled=False,
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        .values("method")
        .annotate(s=Sum("amount"))
    ):
        code = row["method"] or "cash"
        sums[code] = (row["s"] or Decimal("0")).quantize(QTY)
    return dict(sums)


def _expense_outflows_by_method(date_from: date, date_to: date) -> Dict[str, Decimal]:
    sums: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in (
        Expense.objects.filter(
            expense_date__gte=date_from,
            expense_date__lte=date_to,
        )
        .values("payment_method")
        .annotate(s=Sum("amount"))
    ):
        code = row["payment_method"] or "cash"
        sums[code] = (row["s"] or Decimal("0")).quantize(QTY)
    return dict(sums)


def _supplier_outflows_by_method(date_from: date, date_to: date) -> Dict[str, Decimal]:
    sums: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in (
        SupplierPayment.objects.filter(
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        .values("method")
        .annotate(s=Sum("amount"))
    ):
        code = row["method"] or "cash"
        sums[code] = (row["s"] or Decimal("0")).quantize(QTY)
    return dict(sums)


def _treasury_inflows_by_method(date_from: date, date_to: date) -> Dict[str, Decimal]:
    """سندات قبض عميل/موظف من الصندوق الموحّد (لا تُنشئ InvoicePayment)."""
    sums: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    log_from = date_from - timedelta(days=14)
    log_to = date_to + timedelta(days=14)
    logs = AuditLog.objects.filter(
        action=TREASURY_VOUCHER_AUDIT_ACTION,
        created_at__date__gte=log_from,
        created_at__date__lte=log_to,
    ).order_by("created_at", "pk")

    def _add_splits(payload: dict, vd: date) -> None:
        splits = payload.get("payment_splits")
        if splits and isinstance(splits, list):
            for item in splits:
                if not isinstance(item, dict):
                    continue
                m = (item.get("method") or "").strip()
                if not m:
                    continue
                amt = _as_decimal(item.get("amount")).quantize(QTY)
                if amt > 0:
                    sums[m] += amt
        else:
            m = (payload.get("method") or "").strip()
            if m:
                amt = _as_decimal(payload.get("amount")).quantize(QTY)
                if amt > 0:
                    sums[m] += amt

    for log in logs:
        payload = log.payload or {}
        if payload.get("cancelled"):
            continue
        if payload.get("voucher_type") != "receipt":
            continue
        if payload.get("party_type") not in ("customer", "employee"):
            continue
        vd = _parse_voucher_date(payload, log.created_at)
        if not (date_from <= vd <= date_to):
            continue
        _add_splits(payload, vd)

    return {k: v.quantize(QTY) for k, v in sums.items()}


def build_payment_boxes_report(
    date_from: date,
    date_to: date,
    payment_method: Optional[str] = None,
    q: str = "",
) -> dict[str, Any]:
    """
    صف لكل طريقة دفع نقدية/بنكية:
    - افتتاحي: من أول وردية في الفترة
    - وارد: تحصيل فواتير + سندات قبض خزينة
    - صادر: مصروفات + سداد موردين
    - المتبقي: افتتاحي + وارد − صادر
    """
    pm_rows = [r for r in load_payment_method_rows() if r["ledger"] in ("cash", "bank")]
    labels = payment_method_label_map()
    opening_map = {
        r["code"]: get_opening_balance(r["code"], date_from=date_from, date_to=date_to)
        for r in pm_rows
    }
    inv_in = _invoice_inflows_by_method(date_from, date_to)
    tab_in = _tab_inflows_by_method(date_from, date_to)
    tre_in = _treasury_inflows_by_method(date_from, date_to)
    exp_out = _expense_outflows_by_method(date_from, date_to)
    sup_out = _supplier_outflows_by_method(date_from, date_to)

    q_norm = (q or "").strip().lower()
    code_filter = (payment_method or "").strip().lower() or None

    rows: List[dict[str, Any]] = []
    tot_open = tot_in = tot_out = tot_bal = Decimal("0")

    for r in pm_rows:
        code = r["code"]
        if code_filter and code != code_filter:
            continue
        label = r["label_ar"] or labels.get(code, code)
        if q_norm and q_norm not in label.lower() and q_norm not in code.lower():
            continue

        opening = opening_map.get(code, Decimal("0"))
        inflow = (
            inv_in.get(code, Decimal("0"))
            + tab_in.get(code, Decimal("0"))
            + tre_in.get(code, Decimal("0"))
        ).quantize(QTY)
        outflow = (exp_out.get(code, Decimal("0")) + sup_out.get(code, Decimal("0"))).quantize(QTY)
        balance = (opening + inflow - outflow).quantize(QTY)

        rows.append(
            {
                "code": code,
                "label": label,
                "ledger": r["ledger"],
                "opening": opening,
                "inflow": inflow,
                "outflow": outflow,
                "balance": balance,
                "invoice_inflow": inv_in.get(code, Decimal("0")),
                "treasury_inflow": tre_in.get(code, Decimal("0")),
            }
        )
        tot_open += opening
        tot_in += inflow
        tot_out += outflow
        tot_bal += balance

    has_opening_session = bool(opening_map) if uses_shifts() else False
    if uses_shifts():
        opening_note = (
            "الافتتاحي من أول وردية تُفتح ضمن الفترة المحددة."
            if has_opening_session
            else "لا توجد وردية تُفتح ضمن الفترة — الافتتاحي = 0. المتبقي = وارد − صادر للحركات فقط."
        )
    else:
        opening_note = (
            "الافتتاحي من أرصدة الصناديق (إعدادات → أرصدة الصناديق) وتسويات الرصيد حتى اليوم السابق لبداية الفترة."
        )

    return {
        "rows": rows,
        "totals": {
            "opening": tot_open.quantize(QTY),
            "inflow": tot_in.quantize(QTY),
            "outflow": tot_out.quantize(QTY),
            "balance": tot_bal.quantize(QTY),
        },
        "opening_note": opening_note,
        "has_opening_session": has_opening_session,
        "uses_shifts_mode": uses_shifts(),
    }


def build_payment_boxes_cumulative_report(
    as_of: Optional[date] = None,
    payment_method: Optional[str] = None,
    q: str = "",
) -> dict[str, Any]:
    """
    المتبقي النهائي لكل صندوق (كاش/بنك): افتتاحي تراكمي + كل الحركات حتى as_of.

    - مستمر: افتتاحي من إعدادات الصناديق + تسويات حتى as_of.
    - ورديات: افتتاحي أول وردية في السجل + كل الحركات من CUMULATIVE_MOVEMENTS_FROM.
    """
    from django.utils import timezone as django_tz

    date_to = as_of or django_tz.localdate()
    date_from = CUMULATIVE_MOVEMENTS_FROM
    pm_rows = [r for r in load_payment_method_rows() if r["ledger"] in ("cash", "bank")]
    labels = payment_method_label_map()
    inv_in = _invoice_inflows_by_method(date_from, date_to)
    tab_in = _tab_inflows_by_method(date_from, date_to)
    tre_in = _treasury_inflows_by_method(date_from, date_to)
    exp_out = _expense_outflows_by_method(date_from, date_to)
    sup_out = _supplier_outflows_by_method(date_from, date_to)

    q_norm = (q or "").strip().lower()
    code_filter = (payment_method or "").strip().lower() or None

    rows: List[dict[str, Any]] = []
    tot_open = tot_in = tot_out = tot_bal = Decimal("0")

    for r in pm_rows:
        code = r["code"]
        if code_filter and code != code_filter:
            continue
        label = r["label_ar"] or labels.get(code, code)
        if q_norm and q_norm not in label.lower() and q_norm not in code.lower():
            continue

        if uses_shifts():
            opening = get_opening_balance(code, date_from=date_from, date_to=date_to)
        else:
            pm = PaymentMethod.objects.filter(code=code).first()
            opening = continuous_opening_balance(pm, as_of=date_to) if pm else Decimal("0")

        inflow = (
            inv_in.get(code, Decimal("0"))
            + tab_in.get(code, Decimal("0"))
            + tre_in.get(code, Decimal("0"))
        ).quantize(QTY)
        outflow = (exp_out.get(code, Decimal("0")) + sup_out.get(code, Decimal("0"))).quantize(QTY)
        balance = (opening + inflow - outflow).quantize(QTY)

        rows.append(
            {
                "code": code,
                "label": label,
                "ledger": r["ledger"],
                "opening": opening,
                "inflow": inflow,
                "outflow": outflow,
                "balance": balance,
            }
        )
        tot_open += opening
        tot_in += inflow
        tot_out += outflow
        tot_bal += balance

    return {
        "rows": rows,
        "totals": {
            "opening": tot_open.quantize(QTY),
            "inflow": tot_in.quantize(QTY),
            "outflow": tot_out.quantize(QTY),
            "balance": tot_bal.quantize(QTY),
        },
        "as_of": date_to,
        "cumulative": True,
    }


def pos_cashier_balance_snapshot(*, work_session: Optional[WorkSession] = None) -> dict[str, Any]:
    """
    المتبقي النهائي للصناديق في شريط الكاشير — تراكمي (ليس فترة اليوم/الوردية).

    work_session يُمرَّر للتوافق؛ الرصيد لا يُقيَّد بفتح الوردية الحالية.
    """
    from django.utils import timezone as django_tz

    today = django_tz.localdate()
    report = build_payment_boxes_cumulative_report(as_of=today)
    rows = [
        {
            "code": r["code"],
            "label": r["label"],
            "ledger": r["ledger"],
            "balance": r["balance"],
        }
        for r in report["rows"]
    ]
    return {
        "rows": rows,
        "period_label": "الرصيد الحالي",
        "date_from": CUMULATIVE_MOVEMENTS_FROM,
        "date_to": today,
    }
