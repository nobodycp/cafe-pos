"""ربط طرق الدفع بحسابات فرعية في دليل الحسابات."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.accounting.chart_defaults import ensure_default_chart_accounts
from apps.accounting.models import Account
from apps.core.models import PaymentMethod


def _parent_for_ledger(ledger: str) -> Account:
    ensure_default_chart_accounts()
    if ledger == PaymentMethod.Ledger.CASH:
        return Account.objects.get(system_code="CASH")
    return Account.objects.get(system_code="BANK")


def _next_child_code(parent: Account) -> str:
    prefix = f"{parent.code}-"
    siblings = Account.objects.filter(code__startswith=prefix).order_by("-code")
    max_seq = 0
    for acc in siblings:
        tail = acc.code[len(prefix) :]
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))
    return f"{prefix}{max_seq + 1:02d}"


@transaction.atomic
def ensure_gl_account_for_payment_method(pm: PaymentMethod, *, save: bool = True) -> Account | None:
    """يُنشئ حساباً فرعياً لطريقة cash/bank إن لم يكن مربوطاً."""
    ledger = (pm.ledger or "").strip().lower()
    if ledger not in (PaymentMethod.Ledger.CASH, PaymentMethod.Ledger.BANK):
        return None
    if pm.gl_account_id:
        return pm.gl_account
    parent = _parent_for_ledger(ledger)
    code = _next_child_code(parent)
    sys_code = f"PM_{pm.code}"
    acc, _ = Account.objects.update_or_create(
        system_code=sys_code,
        defaults={
            "code": code,
            "name_ar": pm.label_ar or pm.code,
            "name_en": (pm.label_en or "")[:200],
            "account_type": Account.AccountType.ASSET,
            "parent": parent,
            "is_active": True,
        },
    )
    pm.gl_account = acc
    if save:
        pm.save(update_fields=["gl_account"])
    return acc


def ensure_all_payment_method_gl_accounts() -> int:
    """هجرة/صيانة: ربط كل طرق cash/bank النشطة."""
    n = 0
    for pm in PaymentMethod.objects.filter(is_active=True, ledger__in=["cash", "bank"]):
        if not pm.gl_account_id:
            ensure_gl_account_for_payment_method(pm)
            n += 1
    return n


def get_account_for_payment_method(method_code: str) -> Account:
    """حساب القيد لطريقة دفع — فرعي لكل طريقة، أو AR/AP للآجل."""
    from apps.core.payment_methods import resolve_ledger_account_code

    mc = (method_code or "").strip().lower()
    if resolve_ledger_account_code(mc) == "AR":
        ensure_default_chart_accounts()
        return Account.objects.get(system_code="AR")
    pm = PaymentMethod.objects.filter(code=mc).first()
    if pm and pm.gl_account_id:
        return pm.gl_account
    if pm and pm.ledger in (PaymentMethod.Ledger.CASH, PaymentMethod.Ledger.BANK):
        return ensure_gl_account_for_payment_method(pm)
    ledger = resolve_ledger_account_code(mc)
    ensure_default_chart_accounts()
    if ledger == "CASH":
        return Account.objects.get(system_code="CASH")
    if ledger == "AR":
        return Account.objects.get(system_code="AR")
    return Account.objects.get(system_code="BANK")


def opening_balance_equity_account() -> Account:
    ensure_default_chart_accounts()
    acc = Account.objects.filter(system_code="OPENING_BALANCE_EQUITY").first()
    if acc:
        return acc
    parent = Account.objects.filter(system_code="OWNER_CAPITAL").first()
    return Account.objects.create(
        code="3002",
        name_ar="تسويات رصيد افتتاحي",
        name_en="Opening Balance Equity",
        account_type=Account.AccountType.EQUITY,
        parent=parent,
        system_code="OPENING_BALANCE_EQUITY",
        is_active=True,
    )
