from __future__ import annotations

import re
from decimal import Decimal
from django.db import transaction

from apps.core.decimalutil import as_decimal
from apps.core.models import log_audit
from apps.expenses.models import Expense, ExpenseCategory


@transaction.atomic
def create_expense(
    *,
    category: ExpenseCategory,
    amount: Decimal,
    payment_method: str,
    expense_date,
    notes: str = "",
    work_session=None,
    user=None,
    allow_salary_category: bool = False,
) -> Expense:
    amt = as_decimal(amount)
    if amt <= 0:
        raise ValueError("INVALID_AMOUNT")
    if category.code == ExpenseCategory.Code.SALARIES and not allow_salary_category:
        raise ValueError("SALARIES_VIA_PAYROLL_ONLY")

    exp = Expense.objects.create(
        work_session=work_session,
        category=category,
        expense_date=expense_date,
        amount=amt,
        payment_method=payment_method,
        notes=notes,
    )

    from apps.accounting.services import post_expense_journal

    post_expense_journal(expense=exp, user=user)

    log_audit(user, "expense.create", "expenses.Expense", exp.pk, {"amount": str(amt), "category": category.code})
    return exp


def _other_expense_category() -> ExpenseCategory:
    cat, _ = ExpenseCategory.objects.get_or_create(
        code=ExpenseCategory.Code.OTHER,
        defaults={"name_ar": "أخرى", "name_en": "Other"},
    )
    return cat


def resolve_expense_category_from_treasury_note(note: str) -> ExpenseCategory:
    """
    يستنتج تصنيف المصروف من ملاحظة سند الصرف «مصاريف».
    يُجرّب بالترتيب: السطر الأول كاملاً، ثم الجزء قبل | أو — أو :، ثم الرمز، ثم احتواء الاسم (تطابق واحد فقط).
    لا يُختار «رواتب» من هذا المسار (سند الموظف يستخدم التصنيف تلقائياً).
    """
    raw = (note or "").strip()
    qs = ExpenseCategory.objects.exclude(code=ExpenseCategory.Code.SALARIES)
    if not raw:
        return _other_expense_category()

    first_line = raw.split("\n", 1)[0].strip()
    head = re.split(r"[|│\u2014\u2013\-–:]+", first_line, maxsplit=1)[0].strip()
    tokens = []
    for t in (first_line, head):
        if t and t not in tokens:
            tokens.append(t)

    for token in tokens:
        if len(token) < 2:
            continue
        hit = qs.filter(name_ar__iexact=token).first()
        if hit:
            return hit
        code_guess = token.lower().replace(" ", "_").replace("-", "_")
        hit = qs.filter(code__iexact=code_guess).first()
        if hit:
            return hit

    for token in tokens:
        if len(token) < 2:
            continue
        hits = list(qs.filter(name_ar__icontains=token))
        if len(hits) == 1:
            return hits[0]

    return _other_expense_category()


@transaction.atomic
def delete_expense_permanent(*, expense: Expense, user=None) -> None:
    """حذف مصروف وقيوده المحاسبية من النظام."""
    from apps.accounting.models import JournalEntry

    pk = str(expense.pk)
    exp_pk = expense.pk
    refs = JournalEntry.objects.filter(reference_type="expenses.Expense", reference_pk=pk)
    JournalEntry.objects.filter(reversed_by__in=refs).update(reversed_by=None, is_reversed=False)
    refs.delete()
    log_audit(user, "expense.delete", "expenses.Expense", exp_pk, {})
    expense.delete()
