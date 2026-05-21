"""تسوية رصيد صندوق — قيد محاسبي + AuditLog."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.accounting.models import JournalEntry
from apps.accounting.services import _add_line, _build_entry
from apps.core.gl_accounts import get_account_for_payment_method, opening_balance_equity_account
from apps.core.models import BalanceAdjustment, log_audit
from apps.core.payment_channel_balance import get_or_create_channel_balance


@transaction.atomic
def post_balance_adjustment(
    *,
    method,
    amount_delta: Decimal,
    reason: str,
    effective_date,
    user,
) -> BalanceAdjustment:
    from apps.core.gl_accounts import ensure_gl_account_for_payment_method

    ensure_gl_account_for_payment_method(method)
    amt = Decimal(str(amount_delta)).quantize(Decimal("0.01"))
    if amt == 0:
        raise ValueError("ZERO_ADJUSTMENT")

    adj = BalanceAdjustment.objects.create(
        method=method,
        amount_delta=amt,
        reason=(reason or "").strip(),
        effective_date=effective_date,
        created_by=user,
    )

    cash_acc = get_account_for_payment_method(method.code)
    equity_acc = opening_balance_equity_account()
    entry = _build_entry(
        description=f"تسوية رصيد {method.label_ar}: {amt}",
        reference_type="core.BalanceAdjustment",
        reference_pk=adj.pk,
        work_session=None,
        user=user,
        date=effective_date,
    )
    entry.save()
    if amt > 0:
        _add_line(entry, cash_acc, debit=amt, desc=reason[:255] if reason else "تسوية")
        _add_line(entry, equity_acc, credit=amt, desc="تسوية افتتاحي")
    else:
        pos = -amt
        _add_line(entry, cash_acc, credit=pos, desc=reason[:255] if reason else "تسوية")
        _add_line(entry, equity_acc, debit=pos, desc="تسوية افتتاحي")

    adj.journal_entry = entry
    adj.save(update_fields=["journal_entry"])

    bal = get_or_create_channel_balance(method)
    bal.updated_by = user
    bal.save(update_fields=["updated_by", "updated_at"])

    log_audit(
        user,
        "balance_adjustment.create",
        "core.BalanceAdjustment",
        adj.pk,
        {"method": method.code, "amount": str(amt), "date": str(effective_date)},
    )
    return adj
