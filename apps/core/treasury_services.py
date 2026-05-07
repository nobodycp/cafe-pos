"""معالجة سند موحّد: نوع الحركة قبض/صرف، وتصنيف الجهة منفصل."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from apps.contacts.services import record_customer_payment
from apps.core.models import AuditLog, log_audit
from apps.expenses.models import ExpenseCategory
from apps.expenses.services import create_expense, resolve_expense_category_from_treasury_note
from apps.payroll.models import Employee, EmployeeAdvance
from apps.purchasing.services import record_supplier_payment

TREASURY_VOUCHER_AUDIT_ACTION = "treasury.voucher"


def recent_treasury_voucher_logs(*, limit: int = 10):
    """آخر سندات موحّدة مسجّلة في سجل التدقيق."""
    return (
        AuditLog.objects.filter(action=TREASURY_VOUCHER_AUDIT_ACTION)
        .select_related("user")
        .order_by("-created_at")[:limit]
    )


def _log_unified_treasury_voucher(user, payload: dict) -> None:
    log_audit(
        user,
        TREASURY_VOUCHER_AUDIT_ACTION,
        "treasury.UnifiedVoucher",
        "",
        payload,
    )


def submit_treasury_voucher(*, voucher_type: str, cleaned: dict, user, work_session):
    """
    ينفّذ السند حسب النوع.
    يعيد كائناً اختيارياً (قيد عميل، سداد مورد، أو مصروف) للاختبار؛ يكفي None للنجاح.
    """
    amount: Decimal = cleaned["amount"]
    method: str = cleaned["method"]
    note = (cleaned.get("note") or "").strip()
    party_type = cleaned["party_type"]
    payment_lines = cleaned.get("payment_lines")
    voucher_date: date = cleaned["voucher_date"]

    if voucher_type == "receipt" and party_type == "customer":
        cust = cleaned["customer"]
        payer_name = (cleaned.get("payer_name") or "").strip()[:120]
        payer_phone = (cleaned.get("payer_phone") or "").strip()[:40]
        out = record_customer_payment(
            customer=cust,
            amount=amount,
            method=method,
            note=note or "سند قبض",
            user=user,
            work_session=work_session,
            payer_name=payer_name,
            payer_phone=payer_phone,
            payment_lines=payment_lines,
            entry_date=voucher_date,
        )
        split_payload = (
            [{"method": m, "amount": str(a)} for m, a in payment_lines] if payment_lines else None
        )
        _log_unified_treasury_voucher(
            user,
            {
                "voucher_type": voucher_type,
                "party_type": party_type,
                "party_label": cust.name_ar,
                "amount": str(amount),
                "method": method,
                "payment_splits": split_payload,
                "note": (note or "")[:240],
                "payer_name": payer_name,
                "payer_phone": payer_phone,
                "customer_pk": cust.pk,
                "ledger_entry_pk": out.pk,
                "voucher_date": voucher_date.isoformat(),
            },
        )
        return out

    if voucher_type == "disbursement" and party_type == "supplier":
        sup = cleaned["supplier"]
        out = record_supplier_payment(
            supplier=sup,
            amount=amount,
            method=method,
            note=note or "سند صرف",
            user=user,
            work_session=work_session,
            payment_lines=payment_lines,
            entry_date=voucher_date,
        )
        split_payload = (
            [{"method": m, "amount": str(a)} for m, a in payment_lines] if payment_lines else None
        )
        _log_unified_treasury_voucher(
            user,
            {
                "voucher_type": voucher_type,
                "party_type": party_type,
                "party_label": sup.name_ar,
                "amount": str(amount),
                "method": method,
                "payment_splits": split_payload,
                "note": (note or "")[:240],
                "supplier_pk": sup.pk,
                "supplier_payment_pk": out.pk,
                "voucher_date": voucher_date.isoformat(),
            },
        )
        return out

    if voucher_type == "disbursement" and party_type == "employee":
        emp = cleaned["employee"]
        cat = ExpenseCategory.objects.get(code=ExpenseCategory.Code.SALARIES)
        exp = create_expense(
            category=cat,
            amount=amount,
            payment_method=method,
            expense_date=voucher_date,
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
        _log_unified_treasury_voucher(
            user,
            {
                "voucher_type": voucher_type,
                "party_type": party_type,
                "party_label": emp.name_ar,
                "amount": str(amount),
                "method": method,
                "note": (note or "")[:240],
                "employee_pk": emp.pk,
                "expense_pk": exp.pk,
                "voucher_date": voucher_date.isoformat(),
            },
        )
        return exp

    if voucher_type == "disbursement" and party_type == "expense":
        cat = resolve_expense_category_from_treasury_note(note)
        exp = create_expense(
            category=cat,
            amount=amount,
            payment_method=method,
            expense_date=voucher_date,
            notes=note or "سند صرف مصاريف",
            work_session=work_session,
            user=user,
        )
        log_audit(
            user,
            "treasury.expense_voucher",
            "expenses.Expense",
            exp.pk,
            {"amount": str(amount), "category_code": cat.code},
        )
        party_lbl = (getattr(exp.category, "name_ar", None) or "مصروف")[:120]
        _log_unified_treasury_voucher(
            user,
            {
                "voucher_type": voucher_type,
                "party_type": party_type,
                "party_label": party_lbl,
                "amount": str(amount),
                "method": method,
                "note": (note or "")[:240],
                "expense_category_code": cat.code,
                "expense_pk": exp.pk,
                "voucher_date": voucher_date.isoformat(),
            },
        )
        return exp

    raise ValueError("UNKNOWN_VOUCHER_TYPE")


def _employee_earned(emp: Employee) -> Decimal:
    if emp.pay_type == Employee.PayType.MONTHLY:
        return emp.monthly_salary
    if emp.pay_type == Employee.PayType.HOURLY:
        return (emp.work_hours_balance * emp.hourly_wage).quantize(Decimal("0.01"))
    return (emp.work_days_balance * emp.daily_wage).quantize(Decimal("0.01"))
