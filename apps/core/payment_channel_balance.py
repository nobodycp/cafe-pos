"""أرصدة افتتاحية للصناديق — وضع المحاسبة المستمرة."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, Optional

from django.db.models import Sum

from apps.core.models import BalanceAdjustment, PaymentChannelBalance, PaymentMethod
from apps.core.operation_mode import uses_shifts
from apps.core.payment_methods import load_payment_method_rows

QTY = Decimal("0.01")


def _quantize(v: Decimal) -> Decimal:
    return v.quantize(QTY)


def get_or_create_channel_balance(pm: PaymentMethod) -> PaymentChannelBalance:
    bal, _ = PaymentChannelBalance.objects.get_or_create(
        method=pm,
        defaults={"opening_balance": Decimal("0")},
    )
    return bal


def adjustments_sum_for_method(pm: PaymentMethod, *, until: Optional[date] = None) -> Decimal:
    qs = BalanceAdjustment.objects.filter(method=pm)
    if until is not None:
        qs = qs.filter(effective_date__lte=until)
    agg = qs.aggregate(s=Sum("amount_delta"))
    return _quantize(Decimal(str(agg["s"] or "0")))


def continuous_opening_balance(pm: PaymentMethod, *, as_of: Optional[date] = None) -> Decimal:
    """افتتاحي مستمر = رصيد الجدول + تسويات حتى التاريخ."""
    bal = get_or_create_channel_balance(pm)
    adj = adjustments_sum_for_method(pm, until=as_of)
    return _quantize(Decimal(str(bal.opening_balance or 0)) + adj)


def get_opening_balance(
    method_code: str,
    *,
    date_from: date,
    date_to: date,
    work_session=None,
) -> Decimal:
    """مصدر موحّد للافتتاحي حسب نمط العمل."""
    code = (method_code or "").strip().lower()
    if uses_shifts():
        from apps.reports.payment_boxes import opening_balances_at_period_start

        opening_map = opening_balances_at_period_start(date_from, date_to)
        return _quantize(opening_map.get(code, Decimal("0")))
    pm = PaymentMethod.objects.filter(code=code).first()
    if not pm:
        return Decimal("0")
    as_of = date_from - timedelta(days=1) if date_from else None
    return continuous_opening_balance(pm, as_of=as_of)


def channel_balance_rows_for_settings(*, as_of: Optional[date] = None) -> list[dict]:
    if as_of is None:
        as_of = date.today()
    rows = []
    for r in load_payment_method_rows():
        if r["ledger"] not in ("cash", "bank"):
            continue
        pm = PaymentMethod.objects.filter(code=r["code"]).first()
        if not pm:
            continue
        bal = get_or_create_channel_balance(pm)
        adj_sum = adjustments_sum_for_method(pm, until=as_of)
        opening = _quantize(Decimal(str(bal.opening_balance or 0)))
        rows.append(
            {
                "code": pm.code,
                "label": pm.label_ar,
                "opening": opening,
                "adjustments_sum": adj_sum,
                "balance_as_of_today": _quantize(opening + adj_sum),
            }
        )
    return rows
