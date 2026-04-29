"""معالجة سند موحّد: نوع الحركة قبض/صرف، وتصنيف الجهة منفصل."""

from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from apps.contacts.services import record_customer_payment
from apps.core.models import log_audit
from apps.expenses.models import ExpenseCategory
from apps.expenses.services import create_expense
from apps.payroll.models import Employee, EmployeeAdvance
from apps.purchasing.services import record_supplier_payment


def submit_treasury_voucher(*, voucher_type: str, cleaned: dict, user, work_session):
    """
    ينفّذ السند حسب النوع.
    يعيد كائناً اختيارياً (قيد عميل، سداد مورد، أو مصروف) للاختبار؛ يكفي None للنجاح.
    """
    amount: Decimal = cleaned["amount"]
    method: str = cleaned["method"]
    note = (cleaned.get("note") or "").strip()
    party_type = cleaned["party_type"]

    if voucher_type == "receipt" and party_type == "customer":
        out = record_customer_payment(
            customer=cleaned["customer"],
            amount=amount,
            method=method,
            note=note or "سند قبض",
            user=user,
            work_session=work_session,
        )
        return out

    if voucher_type == "disbursement" and party_type == "supplier":
        out = record_supplier_payment(
            supplier=cleaned["supplier"],
            amount=amount,
            method=method,
            note=note or "سند صرف",
            user=user,
            work_session=work_session,
        )
        return out

    if voucher_type == "disbursement" and party_type == "employee":
        emp = cleaned["employee"]
        cat = ExpenseCategory.objects.get(code=ExpenseCategory.Code.SALARIES)
        exp = create_expense(
            category=cat,
            amount=amount,
            payment_method=method,
            expense_date=timezone.localdate(),
            notes=f"صرف راتب / مستحقات: {emp.name_ar}" + (f" — {note}" if note else ""),
            work_session=work_session,
            user=user,
            allow_salary_category=True,
        )
        EmployeeAdvance.objects.create(
            employee=emp,
            work_session=work_session,
            amount=amount,
            note=note or "سند صرف",
            linked_expense=exp,
        )
        emp.advance_balance = (emp.advance_balance + amount).quantize(Decimal("0.01"))
        emp.net_balance = (_employee_earned(emp) - emp.advance_balance - emp.store_purchases_balance).quantize(Decimal("0.01"))
        emp.save(update_fields=["advance_balance", "net_balance", "updated_at"])
        log_audit(
            user,
            "treasury.employee_advance_voucher",
            "expenses.Expense",
            exp.pk,
            {"employee_id": emp.pk, "amount": str(amount)},
        )
        return exp

    if voucher_type == "disbursement" and party_type == "expense":
        cat, _ = ExpenseCategory.objects.get_or_create(
            code=ExpenseCategory.Code.OTHER,
            defaults={"name_ar": "أخرى", "name_en": "Other"},
        )
        exp = create_expense(
            category=cat,
            amount=amount,
            payment_method=method,
            expense_date=timezone.localdate(),
            notes=note or "سند صرف مصاريف",
            work_session=work_session,
            user=user,
        )
        log_audit(
            user,
            "treasury.expense_voucher",
            "expenses.Expense",
            exp.pk,
            {"amount": str(amount)},
        )
        return exp

    raise ValueError("UNKNOWN_VOUCHER_TYPE")


def _employee_earned(emp: Employee) -> Decimal:
    if emp.pay_type == Employee.PayType.MONTHLY:
        return emp.monthly_salary
    if emp.pay_type == Employee.PayType.HOURLY:
        return (emp.work_hours_balance * emp.hourly_wage).quantize(Decimal("0.01"))
    return (emp.work_days_balance * emp.daily_wage).quantize(Decimal("0.01"))
