"""
خدمة المحاسبة: إنشاء قيود يومية تلقائية لكل حدث مالي.
كل دالة تُنشئ قيداً متوازناً (مدين = دائن) وتُعيد JournalEntry.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.core.gl_accounts import get_account_for_payment_method
from apps.core.payment_methods import (
    get_payment_method_codes,
    resolve_ledger_account_code,
)
from apps.core.decimalutil import as_decimal
from apps.core.sequences import next_int


def _next_je_number() -> str:
    return f"JE-{next_int('journal_entry'):06d}"


def _get_account(system_code: str) -> Account:
    acc = Account.objects.filter(system_code=system_code, is_active=True).first()
    if acc:
        return acc
    inactive = Account.objects.filter(system_code=system_code, is_active=False).first()
    if inactive:
        inactive.is_active = True
        inactive.save(update_fields=["is_active", "updated_at"])
        return inactive
    from apps.accounting.chart_defaults import ensure_default_chart_accounts

    ensure_default_chart_accounts()
    acc = Account.objects.filter(system_code=system_code, is_active=True).first()
    if acc:
        return acc
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


def _save_entry_with_unique_number(entry: JournalEntry, *, max_retries: int = 5) -> None:
    """
    يحفظ القيد مع إعادة توليد رقم القيد إذا صادف تعارض unique على entry_number.
    """
    for _ in range(max_retries):
        try:
            with transaction.atomic():
                entry.save()
            return
        except IntegrityError as exc:
            msg = str(exc)
            if "accounting_journalentry.entry_number" not in msg:
                raise
            entry.entry_number = _next_je_number()
    raise IntegrityError("UNIQUE_JE_NUMBER_RETRY_EXHAUSTED")


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


@transaction.atomic
def post_sale_invoice_journal(
    *,
    invoice,
    pay_by_method: Dict[str, Decimal],
    user=None,
    entry_date=None,
) -> JournalEntry:
    """
    قيد بيع: مدين صندوق/بنك/ذمم = الإجمالي، دائن إيرادات + خدمة + ضريبة.
    قيد تكلفة: مدين تكلفة بضاعة مباعة، دائن مخزون.
    تاريخ القيد: يأخذ ``entry_date`` إن مُرّر، وإلا تاريخ إنشاء الفاتورة، وإلا اليوم.
    """
    if JournalEntry.objects.filter(
        reference_type="billing.SaleInvoice",
        reference_pk=str(invoice.pk),
        is_reversed=False,
    ).exclude(description__startswith="عكس قيد").exists():
        return

    grand = as_decimal(invoice.total)
    if grand <= 0:
        raise ValueError("ZERO_INVOICE")

    net = as_decimal(invoice.subtotal) - as_decimal(invoice.discount_total)
    if net < 0:
        net = Decimal("0")
    svc = as_decimal(invoice.service_charge_total)
    tax = as_decimal(invoice.tax_total)
    total_cost = as_decimal(invoice.total_cost)

    if entry_date is None:
        inv_created = getattr(invoice, "created_at", None)
        if inv_created is not None:
            entry_date = timezone.localtime(inv_created).date() if timezone.is_aware(inv_created) else inv_created.date()

    entry = _build_entry(
        description=f"فاتورة بيع {invoice.invoice_number}",
        reference_type="billing.SaleInvoice",
        reference_pk=invoice.pk,
        work_session=invoice.work_session,
        user=user,
        date=entry_date,
    )
    entry.save()

    for method, amount in pay_by_method.items():
        amt = as_decimal(amount)
        if amt <= 0:
            continue
        if resolve_ledger_account_code(method) == "AR":
            continue
        _add_line(entry, get_account_for_payment_method(method), debit=amt, desc=f"تحصيل {method}")

    sales_rev = _get_account("SALES_REVENUE")
    commission_rev = _get_account("COMMISSION_REVENUE")

    commission_total = Decimal("0")
    regular_total = Decimal("0")
    for ln in invoice.lines.select_related("product"):
        p = ln.product
        if p.product_type == p.ProductType.COMMISSION:
            commission_total += as_decimal(ln.recognized_revenue)
        else:
            regular_total += as_decimal(ln.recognized_revenue)

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
            date=entry_date,
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

    total = as_decimal(purchase_invoice.total)
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
        amt = as_decimal(amount)
        if amt <= 0:
            continue
        if method == "credit" or resolve_ledger_account_code(method) == "AR":
            _add_line(entry, _get_account("AP"), credit=amt, desc="ذمم مورد")
        else:
            _add_line(entry, get_account_for_payment_method(method), credit=amt, desc="دفع")

    return entry


def _expense_credit_account(method_code: str):
    """حساب الدائن لمصروف: صندوق فرعي / بنك فرعي / ذمم دائنة للآجل."""
    if resolve_ledger_account_code(method_code) == "AR":
        return _get_account("AP")
    return get_account_for_payment_method(method_code)


def _expense_credit_buckets_from_model(expense) -> Dict[str, Decimal]:
    """يجمع مبالغ الدائن حسب رمز طريقة الدفع."""
    amount = as_decimal(expense.amount)
    pm = (expense.payment_method or "").strip().lower()
    raw = (getattr(expense, "payment_splits_json", None) or "").strip()
    by_method: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    codes_ok = get_payment_method_codes()

    if pm == "split":
        if not raw:
            raise ValueError("INVALID_EXPENSE_SPLITS")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("INVALID_EXPENSE_SPLITS_JSON") from exc
        if not isinstance(data, list):
            raise ValueError("INVALID_EXPENSE_SPLITS_JSON")
        for item in data:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                code = str(item[0] or "").strip().lower()
                amt_raw = item[1]
            elif isinstance(item, dict):
                code = str(item.get("method") or "").strip().lower()
                amt_raw = item.get("amount")
            else:
                continue
            if not code or code not in codes_ok:
                raise ValueError("INVALID_EXPENSE_SPLIT_METHOD")
            a = as_decimal(amt_raw)
            if a <= 0:
                continue
            by_method[code] += a.quantize(Decimal("0.01"))
    else:
        by_method[pm] += amount.quantize(Decimal("0.01"))

    return dict(by_method)


@transaction.atomic
def post_expense_journal(
    *,
    expense,
    user=None,
) -> JournalEntry:
    """قيد مصروف: مدين حساب المصروف، دائن صندوق/بنك/ذمم (دفع واحد أو مختلط)."""
    amount = as_decimal(expense.amount)
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
        date=expense.expense_date,
    )
    entry.save()

    _add_line(entry, exp_account, debit=amount, desc=expense.notes[:255] if expense.notes else "")

    buckets = _expense_credit_buckets_from_model(expense)
    if not buckets:
        raise ValueError("EXPENSE_PAYMENT_EMPTY")
    total_credit = sum(as_decimal(v) for v in buckets.values()).quantize(Decimal("0.01"))
    if total_credit != amount.quantize(Decimal("0.01")):
        raise ValueError("EXPENSE_PAYMENT_MISMATCH")
    for method_code, cred in sorted(buckets.items()):
        if cred <= 0:
            continue
        _add_line(entry, _expense_credit_account(method_code), credit=cred, desc="دفع مصروف")

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
    entry_date: Optional[date] = None,
) -> JournalEntry:
    """قيد تحصيل من عميل: مدين صندوق/بنك، دائن ذمم زبائن."""
    amt = as_decimal(amount)
    if amt <= 0:
        raise ValueError("ZERO_PAYMENT")

    entry = _build_entry(
        description=f"تحصيل من {customer.name_ar} — {amt}",
        reference_type=reference_type,
        reference_pk=reference_pk,
        work_session=work_session,
        user=user,
        date=entry_date,
    )
    entry.save()

    _add_line(entry, get_account_for_payment_method(method), debit=amt, desc="تحصيل")
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
    entry_date: Optional[date] = None,
) -> JournalEntry:
    """قيد سداد مورد: مدين ذمم موردين، دائن صندوق/بنك."""
    amt = as_decimal(amount)
    if amt <= 0:
        raise ValueError("ZERO_PAYMENT")

    entry = _build_entry(
        description=f"سداد لـ {supplier.name_ar} — {amt}",
        reference_type=reference_type,
        reference_pk=reference_pk,
        work_session=work_session,
        user=user,
        date=entry_date,
    )
    entry.save()

    _add_line(entry, _get_account("AP"), debit=amt, desc=f"تسوية ذمم {supplier.name_ar}")
    _add_line(entry, get_account_for_payment_method(method), credit=amt, desc="سداد")

    return entry


@transaction.atomic
def post_customer_payment_journal_multi(
    *,
    customer,
    payments: List[Tuple[str, Decimal]],
    reference_type: str = "",
    reference_pk: str = "",
    work_session=None,
    user=None,
    entry_date: Optional[date] = None,
) -> JournalEntry:
    """تحصيل من عميل بعدة طرق: مدين صندوق/بنك (مجمّع)، دائن ذمم زبائن = مجموع المحصّل فقط (بدون آجل)."""
    debits: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    total_collected = Decimal("0")
    for method, amount in payments:
        amt = as_decimal(amount)
        if amt <= 0:
            continue
        if resolve_ledger_account_code(method) == "AR":
            continue
        debits[method] += amt
        total_collected += amt
    total_collected = total_collected.quantize(Decimal("0.01"))
    if total_collected <= 0:
        raise ValueError("ZERO_PAYMENT")

    entry = _build_entry(
        description=f"تحصيل من {customer.name_ar} — {total_collected}",
        reference_type=reference_type,
        reference_pk=reference_pk,
        work_session=work_session,
        user=user,
        date=entry_date,
    )
    entry.save()

    for method_code, sub in debits.items():
        sub = sub.quantize(Decimal("0.01"))
        if sub <= 0:
            continue
        _add_line(entry, get_account_for_payment_method(method_code), debit=sub, desc="تحصيل")
    _add_line(entry, _get_account("AR"), credit=total_collected, desc=f"خصم ذمم {customer.name_ar}")

    return entry


@transaction.atomic
def post_employee_debt_repayment_journal(
    *,
    employee,
    amount: Decimal,
    method: str,
    reference_type: str = "",
    reference_pk: str = "",
    work_session=None,
    user=None,
    entry_date: Optional[date] = None,
) -> JournalEntry:
    """قيد تحصيل من موظف (سداد ذمة): مدين صندوق/بنك، دائن رواتب (تخفيض مصروف / تسوية ذمة)."""
    amt = as_decimal(amount)
    if amt <= 0:
        raise ValueError("ZERO_PAYMENT")

    entry = _build_entry(
        description=f"سداد ذمة موظف {employee.name_ar} — {amt}",
        reference_type=reference_type,
        reference_pk=reference_pk,
        work_session=work_session,
        user=user,
        date=entry_date,
    )
    entry.save()

    _add_line(entry, get_account_for_payment_method(method), debit=amt, desc="تحصيل من موظف")
    _add_line(entry, _get_account("EXP_SALARIES"), credit=amt, desc=f"تسوية ذمة {employee.name_ar}")

    return entry


@transaction.atomic
def post_employee_debt_repayment_journal_multi(
    *,
    employee,
    payments: List[Tuple[str, Decimal]],
    reference_type: str = "",
    reference_pk: str = "",
    work_session=None,
    user=None,
    entry_date: Optional[date] = None,
) -> JournalEntry:
    """سداد ذمة موظف بعدة طرق دفع: مدين صندوق/بنك لكل شريحة، دائن رواتب = مجموع المحصّل (بدون آجل)."""
    debits: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    total_collected = Decimal("0")
    for method, amount in payments:
        amt = as_decimal(amount)
        if amt <= 0:
            continue
        if resolve_ledger_account_code(method) == "AR":
            continue
        debits[method] += amt
        total_collected += amt
    total_collected = total_collected.quantize(Decimal("0.01"))
    if total_collected <= 0:
        raise ValueError("ZERO_PAYMENT")

    entry = _build_entry(
        description=f"سداد ذمة موظف {employee.name_ar} — {total_collected}",
        reference_type=reference_type,
        reference_pk=reference_pk,
        work_session=work_session,
        user=user,
        date=entry_date,
    )
    entry.save()

    for method_code, sub in debits.items():
        sub = sub.quantize(Decimal("0.01"))
        if sub <= 0:
            continue
        _add_line(entry, get_account_for_payment_method(method_code), debit=sub, desc="تحصيل من موظف")
    _add_line(entry, _get_account("EXP_SALARIES"), credit=total_collected, desc=f"تسوية ذمة {employee.name_ar}")

    return entry


@transaction.atomic
def post_supplier_payment_journal_multi(
    *,
    supplier,
    payments: List[Tuple[str, Decimal]],
    reference_type: str = "",
    reference_pk: str = "",
    work_session=None,
    user=None,
    entry_date: Optional[date] = None,
) -> JournalEntry:
    """سداد مورد بعدة طرق: مدين ذمم موردين = المحصّل، دائن صندوق/بنك لكل شريحة (بدون آجل)."""
    credits: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    total_paid = Decimal("0")
    for method, amount in payments:
        amt = as_decimal(amount)
        if amt <= 0:
            continue
        if resolve_ledger_account_code(method) == "AR":
            continue
        credits[method] += amt
        total_paid += amt
    total_paid = total_paid.quantize(Decimal("0.01"))
    if total_paid <= 0:
        raise ValueError("ZERO_PAYMENT")

    entry = _build_entry(
        description=f"سداد لـ {supplier.name_ar} — {total_paid}",
        reference_type=reference_type,
        reference_pk=reference_pk,
        work_session=work_session,
        user=user,
        date=entry_date,
    )
    entry.save()

    _add_line(entry, _get_account("AP"), debit=total_paid, desc=f"تسوية ذمم {supplier.name_ar}")
    for method_code, sub in credits.items():
        sub = sub.quantize(Decimal("0.01"))
        if sub <= 0:
            continue
        _add_line(entry, get_account_for_payment_method(method_code), credit=sub, desc="سداد")

    return entry


MANUAL_ENTRY_REFERENCE = "accounting.ManualEntry"


@transaction.atomic
def create_manual_journal_entry(
    *,
    date,
    description: str,
    lines: List[Tuple[Account, Decimal, Decimal, str]],
    user=None,
) -> JournalEntry:
    """قيد يدوي متعدد الأسطر — مدين = دائن."""
    entry = _build_entry(
        description=description,
        reference_type=MANUAL_ENTRY_REFERENCE,
        user=user,
        date=date,
    )
    entry.save()
    for account, debit, credit, line_desc in lines:
        _add_line(entry, account, debit=debit, credit=credit, desc=line_desc)
    if not entry.is_balanced:
        raise ValueError("UNBALANCED_ENTRY")
    return entry


@transaction.atomic
def create_manual_transfer_entry(
    *,
    date,
    description: str,
    from_account: Account,
    to_account: Account,
    amount: Decimal,
    user=None,
) -> JournalEntry:
    """نقل مبلغ بين حسابين: مدين الوجهة، دائن المصدر."""
    amt = as_decimal(amount).quantize(Decimal("0.01"))
    if amt <= 0:
        raise ValueError("ZERO_AMOUNT")
    if from_account.pk == to_account.pk:
        raise ValueError("SAME_ACCOUNT")
    return create_manual_journal_entry(
        date=date,
        description=description,
        lines=[
            (to_account, amt, Decimal("0"), description),
            (from_account, Decimal("0"), amt, description),
        ],
        user=user,
    )


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
    _save_entry_with_unique_number(rev)

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
    """ميزان المراجعة: رصيد كل حساب له قيود أو حساب نشط في الدليل."""
    from django.db.models import Q, Sum

    account_ids = list(JournalLine.objects.values_list("account_id", flat=True).distinct())
    if account_ids:
        acc_qs = Account.objects.filter(Q(is_active=True) | Q(pk__in=account_ids)).distinct().order_by("code")
    else:
        acc_qs = Account.objects.filter(is_active=True).order_by("code")

    rows = []
    for acc in acc_qs:
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


def trial_balance_grand_totals() -> Tuple[Decimal, Decimal]:
    """إجمالي مدين/دائن جميع أسطر القيود (يجب أن يتطابقا في دفتر متوازن)."""
    from django.db.models import Sum

    agg = JournalLine.objects.aggregate(d=Sum("debit"), c=Sum("credit"))
    d = agg["d"] or Decimal("0")
    c = agg["c"] or Decimal("0")
    return d.quantize(Decimal("0.01")), c.quantize(Decimal("0.01"))


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


def journal_line_delta(line: JournalLine, *, is_debit_normal: bool) -> Decimal:
    if is_debit_normal:
        return as_decimal(line.debit) - as_decimal(line.credit)
    return as_decimal(line.credit) - as_decimal(line.debit)


def paginated_account_ledger_context(
    account: Account,
    *,
    lines_qs,
    date_from,
    date_to,
    page,
    start_idx: int,
) -> dict:
    """
    يبني صفوف كشف الحساب مع رصيد جاري للصفحة الحالية (نفس منطق account_ledger_view).
    """
    is_debit_normal = account.account_type in (Account.AccountType.ASSET, Account.AccountType.EXPENSE)

    opening_at_period = Decimal("0")
    if date_from:
        prior = (
            JournalLine.objects.filter(account=account, entry__date__lt=date_from)
            .select_related("entry")
            .order_by("entry__date", "entry__created_at", "pk")
        )
        for line in prior:
            opening_at_period += journal_line_delta(line, is_debit_normal=is_debit_normal)
        opening_at_period = opening_at_period.quantize(Decimal("0.01"))

    prior_n = max(0, start_idx - 1) if page.object_list else 0
    running = opening_at_period
    if prior_n:
        for line in lines_qs[:prior_n]:
            running += journal_line_delta(line, is_debit_normal=is_debit_normal)
        running = running.quantize(Decimal("0.01"))

    rows = []
    for line in page:
        running = (running + journal_line_delta(line, is_debit_normal=is_debit_normal)).quantize(Decimal("0.01"))
        rows.append({
            "date": line.entry.date,
            "entry_pk": line.entry.pk,
            "entry_number": line.entry.entry_number,
            "description": line.description or line.entry.description,
            "debit": line.debit,
            "credit": line.credit,
            "balance": running,
        })

    closing = opening_at_period
    for line in lines_qs:
        closing += journal_line_delta(line, is_debit_normal=is_debit_normal)
    closing = closing.quantize(Decimal("0.01"))

    return {
        "rows": rows,
        "opening_balance": opening_at_period,
        "closing_balance": closing,
        "page_opening_balance": running if page.object_list else opening_at_period,
    }


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
            running += as_decimal(line.debit) - as_decimal(line.credit)
        else:
            running += as_decimal(line.credit) - as_decimal(line.debit)
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
