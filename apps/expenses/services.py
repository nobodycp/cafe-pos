from __future__ import annotations

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
