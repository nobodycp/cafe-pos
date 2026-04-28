"""
خدمة المحاسبة: إنشاء قيود يومية تلقائية لكل حدث مالي.
كل دالة تُنشئ قيداً متوازناً (مدين = دائن) وتُعيد JournalEntry.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from django.db import transaction
from django.utils import timezone

from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.core.sequences import next_int


def _d(v) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _next_je_number() -> str:
    return f"JE-{next_int('journal_entry'):06d}"


def _get_account(system_code: str) -> Account:
    try:
        return Account.objects.get(system_code=system_code, is_active=True)
    except Account.DoesNotExist:
        raise ValueError(f"ACCOUNT_NOT_FOUND:{system_code}")


def _build_entry(
    *,
    description: str,
    reference_type: str = "",
    reference_pk: str = "",
    work_session=None,
    user=None,
    date=None,
) -> JournalEntry:
    return JournalEntry(
        entry_number=_next_je_number(),
        date=date or timezone.now().date(),
        description=description,
        reference_type=reference_type,
        reference_pk=str(reference_pk) if reference_pk else "",
        work_session=work_session,
        user=user,
    )


def _add_line(entry: JournalEntry, account: Account, debit: Decimal = Decimal("0"), credit: Decimal = Decimal("0"), desc: str = ""):
    if debit <= 0 and credit <= 0:
        return
    JournalLine.objects.create(
        entry=entry,
        account=account,
        debit=debit.quantize(Decimal("0.01")),
        credit=credit.quantize(Decimal("0.01")),
        description=desc,
    )


PAYMENT_ACCOUNT_MAP = {
    "cash": "CASH",
    "bank": "BANK",
    "bank_ps": "BANK",
    "palpay": "BANK",
    "jawwalpay": "BANK",
    "credit": "AR",
}


@transaction.atomic
def post_sale_invoice_journal(
    *,
    invoice,
    pay_by_method: Dict[str, Decimal],
    user=None,
) -> JournalEntry:
    """
    قيد بيع: مدين صندوق/بنك/ذمم = الإجمالي، دائن إيرادات + خدمة + ضريبة.
    قيد تكلفة: مدين تكلفة بضاعة مباعة، دائن مخزون.
    """
    if JournalEntry.objects.filter(
        reference_type="billing.SaleInvoice",
        reference_pk=str(invoice.pk),
    ).exists():
        return

    grand = _d(invoice.total)
    if grand <= 0:
        raise ValueError("ZERO_INVOICE")

    net = _d(invoice.subtotal) - _d(invoice.discount_total)
    if net < 0:
        net = Decimal("0")
    svc = _d(invoice.service_charge_total)
    tax = _d(invoice.tax_total)
    total_cost = _d(invoice.total_cost)

    entry = _build_entry(
        description=f"فاتورة بيع {invoice.invoice_number}",
        reference_type="billing.SaleInvoice",
        reference_pk=invoice.pk,
        work_session=invoice.work_session,
        user=user,
    )
    entry.save()

    for method, amount in pay_by_method.items():
        amt = _d(amount)
        if amt <= 0:
            continue
        sys_code = PAYMENT_ACCOUNT_MAP.get(method)
        if not sys_code:
            continue
        _add_line(entry, _get_account(sys_code), debit=amt, desc=f"تحصيل {method}")

    sales_rev = _get_account("SALES_REVENUE")
    commission_rev = _get_account("COMMISSION_REVENUE")

    commission_total = Decimal("0")
    regular_total = Decimal("0")
    for ln in invoice.lines.select_related("product"):
        p = ln.product
        if p.product_type == p.ProductType.COMMISSION:
            commission_total += _d(ln.recognized_revenue)
        else:
            regular_total += _d(ln.recognized_revenue)

    regular_revenue = net - commission_total
    if regular_revenue < 0:
        regular_revenue = Decimal("0")

    if regular_revenue > 0:
        _add_line(entry, sales_rev, credit=regular_revenue, desc="إيرادات مبيعات")
    if commission_total > 0:
        _add_line(entry, commission_rev, credit=commission_total, desc="إيرادات عمولة")
    if svc > 0:
        _add_line(entry, _get_account("SERVICE_REVENUE"), credit=svc, desc="رسم خدمة")
    if tax > 0:
        _add_line(entry, _get_account("TAX_PAYABLE"), credit=tax, desc="ضريبة مستحقة")

    rounding = grand - (regular_revenue + commission_total + svc + tax)
    if abs(rounding) >= Decimal("0.01"):
        if rounding > 0:
            _add_line(entry, sales_rev, credit=rounding, desc="تقريب")
        else:
            _add_line(entry, sales_rev, debit=-rounding, desc="تقريب")

    if total_cost > 0:
        cogs_entry = _build_entry(
            description=f"تكلفة بضاعة مباعة — {invoice.invoice_number}",
            reference_type="billing.SaleInvoice",
            reference_pk=invoice.pk,
            work_session=invoice.work_session,
            user=user,
        )
        cogs_entry.save()
        _add_line(cogs_entry, _get_account("COGS"), debit=total_cost, desc="تكلفة مبيعات")
        _add_line(cogs_entry, _get_account("INVENTORY"), credit=total_cost, desc="خصم مخزون")

    return entry


@transaction.atomic
def post_purchase_invoice_journal(
    *,
    purchase_invoice,
    pay_by_method: Dict[str, Decimal],
    user=None,
) -> JournalEntry:
    """قيد شراء: مدين مخزون، دائن صندوق/بنك/ذمم موردين."""
    existing = JournalEntry.objects.filter(
        reference_type="purchasing.PurchaseInvoice",
        reference_pk=str(purchase_invoice.pk),
    ).exists()
    if existing:
        return

    total = _d(purchase_invoice.total)
    if total <= 0:
        raise ValueError("ZERO_PURCHASE")

    entry = _build_entry(
        description=f"فاتورة شراء {purchase_invoice.invoice_number}",
        reference_type="purchasing.PurchaseInvoice",
        reference_pk=purchase_invoice.pk,
        work_session=purchase_invoice.work_session,
        user=user,
    )
    entry.save()

    _add_line(entry, _get_account("INVENTORY"), debit=total, desc="إضافة مخزون")

    for method, amount in pay_by_method.items():
        amt = _d(amount)
        if amt <= 0:
            continue
        if method == "credit":
            _add_line(entry, _get_account("AP"), credit=amt, desc="ذمم مورد")
        elif method == "cash":
            _add_line(entry, _get_account("CASH"), credit=amt, desc="دفع نقدي")
        elif method == "bank":
            _add_line(entry, _get_account("BANK"), credit=amt, desc="دفع بنكي")

    return entry


@transaction.atomic
def post_expense_journal(
    *,
    expense,
    user=None,
) -> JournalEntry:
    """قيد مصروف: مدين حساب المصروف، دائن صندوق/بنك."""
    amount = _d(expense.amount)
    if amount <= 0:
        raise ValueError("ZERO_EXPENSE")

    cat_code = expense.category.code if expense.category else "other"
    sys_code = f"EXP_{cat_code.upper()}"
    try:
        exp_account = _get_account(sys_code)
    except ValueError:
        exp_account = _get_account("EXP_OTHER")

    entry = _build_entry(
        description=f"مصروف: {expense.category.name_ar if expense.category else ''} — {amount}",
        reference_type="expenses.Expense",
        reference_pk=expense.pk,
        work_session=expense.work_session,
        user=user,
    )
    entry.save()

    _add_line(entry, exp_account, debit=amount, desc=expense.notes[:255] if expense.notes else "")

    pay_sys = "CASH" if expense.payment_method == "cash" else "BANK"
    _add_line(entry, _get_account(pay_sys), credit=amount, desc="دفع مصروف")

    return entry


@transaction.atomic
def post_customer_payment_journal(
    *,
    customer,
    amount: Decimal,
    method: str,
    reference_type: str = "",
    reference_pk: str = "",
    work_session=None,
    user=None,
) -> JournalEntry:
    """قيد تحصيل من عميل: مدين صندوق/بنك، دائن ذمم زبائن."""
    amt = _d(amount)
    if amt <= 0:
        raise ValueError("ZERO_PAYMENT")

    entry = _build_entry(
        description=f"تحصيل من {customer.name_ar} — {amt}",
        reference_type=reference_type,
        reference_pk=reference_pk,
        work_session=work_session,
        user=user,
    )
    entry.save()

    pay_sys = "CASH" if method == "cash" else "BANK"
    _add_line(entry, _get_account(pay_sys), debit=amt, desc="تحصيل")
    _add_line(entry, _get_account("AR"), credit=amt, desc=f"خصم ذمم {customer.name_ar}")

    return entry


@transaction.atomic
def post_supplier_payment_journal(
    *,
    supplier,
    amount: Decimal,
    method: str,
    reference_type: str = "",
    reference_pk: str = "",
    work_session=None,
    user=None,
) -> JournalEntry:
    """قيد سداد مورد: مدين ذمم موردين، دائن صندوق/بنك."""
    amt = _d(amount)
    if amt <= 0:
        raise ValueError("ZERO_PAYMENT")

    entry = _build_entry(
        description=f"سداد لـ {supplier.name_ar} — {amt}",
        reference_type=reference_type,
        reference_pk=reference_pk,
        work_session=work_session,
        user=user,
    )
    entry.save()

    _add_line(entry, _get_account("AP"), debit=amt, desc=f"تسوية ذمم {supplier.name_ar}")
    pay_sys = "CASH" if method == "cash" else "BANK"
    _add_line(entry, _get_account(pay_sys), credit=amt, desc="سداد")

    return entry


@transaction.atomic
def reverse_journal_entry(*, original: JournalEntry, reason: str = "", user=None) -> JournalEntry:
    """عكس قيد: ينشئ قيداً معاكساً ويُعلّم الأصلي كمعكوس."""
    if original.is_reversed:
        raise ValueError("ALREADY_REVERSED")

    rev = _build_entry(
        description=f"عكس قيد {original.entry_number}: {reason}",
        reference_type=original.reference_type,
        reference_pk=original.reference_pk,
        work_session=original.work_session,
        user=user,
    )
    rev.save()

    for line in original.lines.all():
        JournalLine.objects.create(
            entry=rev,
            account=line.account,
            debit=line.credit,
            credit=line.debit,
            description=f"عكس: {line.description}",
        )

    original.is_reversed = True
    original.reversed_by = rev
    original.save(update_fields=["is_reversed", "reversed_by", "updated_at"])

    return rev


def trial_balance() -> List[dict]:
    """ميزان المراجعة: رصيد كل حساب نشط."""
    from django.db.models import Sum

    rows = []
    for acc in Account.objects.filter(is_active=True).order_by("code"):
        agg = acc.journal_lines.aggregate(d=Sum("debit"), c=Sum("credit"))
        total_d = agg["d"] or Decimal("0")
        total_c = agg["c"] or Decimal("0")
        balance = acc.computed_balance
        rows.append({
            "account": acc,
            "total_debit": total_d,
            "total_credit": total_c,
            "balance": balance,
        })
    return rows


def profit_and_loss(date_from=None, date_to=None) -> dict:
    """تقرير الأرباح والخسائر."""
    from django.db.models import Q, Sum

    q = Q()
    if date_from:
        q &= Q(entry__date__gte=date_from)
    if date_to:
        q &= Q(entry__date__lte=date_to)

    def _sum_type(account_type):
        qs = JournalLine.objects.filter(q, account__account_type=account_type, account__is_active=True)
        agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
        total_d = agg["d"] or Decimal("0")
        total_c = agg["c"] or Decimal("0")
        if account_type in ("revenue",):
            return (total_c - total_d).quantize(Decimal("0.01"))
        return (total_d - total_c).quantize(Decimal("0.01"))

    revenue = _sum_type("revenue")
    expenses = _sum_type("expense")
    net = (revenue - expenses).quantize(Decimal("0.01"))

    return {"revenue": revenue, "expenses": expenses, "net_income": net}


def account_ledger(account: Account, date_from=None, date_to=None) -> List[dict]:
    """كشف حساب مفصّل."""
    from django.db.models import Q

    q = Q(account=account)
    if date_from:
        q &= Q(entry__date__gte=date_from)
    if date_to:
        q &= Q(entry__date__lte=date_to)

    rows = []
    running = Decimal("0")
    is_debit_normal = account.account_type in (Account.AccountType.ASSET, Account.AccountType.EXPENSE)

    for line in JournalLine.objects.filter(q).select_related("entry").order_by("entry__date", "entry__created_at"):
        if is_debit_normal:
            running += _d(line.debit) - _d(line.credit)
        else:
            running += _d(line.credit) - _d(line.debit)
        rows.append({
            "date": line.entry.date,
            "entry_pk": line.entry.pk,
            "entry_number": line.entry.entry_number,
            "description": line.description or line.entry.description,
            "debit": line.debit,
            "credit": line.credit,
            "balance": running.quantize(Decimal("0.01")),
        })
    return rows
