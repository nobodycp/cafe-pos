"""منطق أرصدة الموظفين وسداد الذمم."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple

from django.db import transaction

from apps.core.decimalutil import as_decimal
from apps.core.payment_methods import resolve_ledger_account_code
from apps.payroll.models import Employee, EmployeeDebtRepayment


def recalc_employee_net_balance(emp: Employee) -> None:
    """يحدّث حقل صافي الرصيد من المستحقات ناقص السلف ومشتريات المقهى."""
    if emp.pay_type == Employee.PayType.MONTHLY:
        earned = as_decimal(emp.monthly_salary)
    elif emp.pay_type == Employee.PayType.HOURLY:
        earned = emp.work_hours_balance * as_decimal(emp.hourly_wage)
    else:
        earned = emp.work_days_balance * as_decimal(emp.daily_wage)
    emp.net_balance = (earned - as_decimal(emp.advance_balance) - as_decimal(emp.store_purchases_balance)).quantize(
        Decimal("0.01")
    )
    emp.save(update_fields=["net_balance", "updated_at"])


@transaction.atomic
def record_employee_debt_repayment(
    *,
    employee: Employee,
    amount: Decimal,
    method: str,
    note: str,
    user,
    work_session,
    voucher_date: date,
    payment_lines: Optional[List[Tuple[str, Decimal]]] = None,
) -> EmployeeDebtRepayment:
    """
    تسديد ذمة موظف للمحل (مشتريات مقهى ثم سلف معلّقة).
    يحدّث الأرصدة ويُنشئ قيداً محاسبياً وسجلاً في EmployeeDebtRepayment.
    يدعم تقسيم التحصيل على أكثر من طريقة دفع (مثل سند قبض العميل).
    """
    from apps.accounting.services import (
        post_employee_debt_repayment_journal,
        post_employee_debt_repayment_journal_multi,
    )
    from apps.core.models import log_audit

    amt = as_decimal(amount).quantize(Decimal("0.01"))
    if amt <= 0:
        raise ValueError("INVALID_AMOUNT")

    lines: List[Tuple[str, Decimal]] = []
    if payment_lines is not None:
        for m, a in payment_lines:
            mc = str(m or "").strip().lower()
            a2 = as_decimal(a)
            if a2 <= 0 or not mc:
                continue
            lines.append((mc, a2))
    else:
        lines = [(str(method or "cash").strip().lower(), amt)]

    if not lines:
        raise ValueError("INVALID_AMOUNT")

    sum_lines = sum(a for _, a in lines).quantize(Decimal("0.01"))
    if sum_lines != amt:
        raise ValueError("PAYMENT_LINES_SUM_MISMATCH")

    pay_collected = sum(
        a for m, a in lines if resolve_ledger_account_code(m) != "AR"
    ).quantize(Decimal("0.01"))
    if pay_collected <= 0:
        raise ValueError("INVALID_AMOUNT")

    store = as_decimal(employee.store_purchases_balance).quantize(Decimal("0.01"))
    adv = as_decimal(employee.advance_balance).quantize(Decimal("0.01"))

    # ذمة المقهى تُسدّ أولاً، ثم السلف الموجبة؛ أي مبلغ زائد (أو عدم وجود ذمة)
    # يُسجَّل بتخفيض advance_balance دون أرضية — سالب = رصيد لصالح الموظف (تسبّق للمحل).
    from_store = min(amt, store)
    from_adv = (amt - from_store).quantize(Decimal("0.01"))

    method_stored = lines[0][0] if len(lines) == 1 else "split"

    employee.store_purchases_balance = (store - from_store).quantize(Decimal("0.01"))
    employee.advance_balance = (adv - from_adv).quantize(Decimal("0.01"))
    if employee.store_purchases_balance < 0 and employee.store_purchases_balance > Decimal("-0.01"):
        employee.store_purchases_balance = Decimal("0")
    # advance_balance سالب مسموح (رصيد موجّب للموظف مقابل المحل)
    employee.save(update_fields=["store_purchases_balance", "advance_balance", "updated_at"])
    recalc_employee_net_balance(employee)

    note_clean = (note or "").strip()[:500]
    if len(lines) > 1:
        split_txt = " · ".join(f"{m}:{a}" for m, a in lines)
        note_clean = (f"{note_clean} — [{split_txt}]" if note_clean else f"[{split_txt}]")[:500]

    rep = EmployeeDebtRepayment.objects.create(
        employee=employee,
        work_session=work_session,
        amount=amt,
        method=method_stored[:32],
        store_portion=from_store,
        advance_portion=from_adv,
        note=note_clean,
    )

    use_multi = len(lines) > 1 or any(
        resolve_ledger_account_code(m) == "AR" and a > 0 for m, a in lines
    )
    if not use_multi:
        je = post_employee_debt_repayment_journal(
            employee=employee,
            amount=lines[0][1],
            method=lines[0][0],
            reference_type="payroll.EmployeeDebtRepayment",
            reference_pk=str(rep.pk),
            work_session=work_session,
            user=user,
            entry_date=voucher_date,
        )
    else:
        je = post_employee_debt_repayment_journal_multi(
            employee=employee,
            payments=lines,
            reference_type="payroll.EmployeeDebtRepayment",
            reference_pk=str(rep.pk),
            work_session=work_session,
            user=user,
            entry_date=voucher_date,
        )
    if je is not None:
        rep.journal_entry = je
        rep.save(update_fields=["journal_entry", "updated_at"])

    log_audit(
        user,
        "payroll.employee_debt_repayment",
        "payroll.EmployeeDebtRepayment",
        str(rep.pk),
        {
            "employee_pk": employee.pk,
            "amount": str(amt),
            "store_portion": str(from_store),
            "advance_portion": str(from_adv),
            "payment_splits": [{"method": m, "amount": str(a)} for m, a in lines] if len(lines) > 1 else None,
        },
    )
    return rep
