from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.core.models import log_audit
from apps.expenses.models import Expense, ExpenseCategory


def _d(v) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


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
) -> Expense:
    amt = _d(amount)
    if amt <= 0:
        raise ValueError("INVALID_AMOUNT")

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
