"""معالجة سند موحّد: نوع الحركة قبض/صرف، وتصنيف الجهة منفصل."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from apps.contacts.models import Customer
from apps.contacts.services import record_customer_payment
from apps.core.models import AuditLog, log_audit
from apps.core.payment_methods import get_payment_method_codes
from apps.expenses.models import ExpenseCategory
from apps.expenses.services import create_expense, resolve_expense_category_from_treasury_note
from apps.payroll.models import Employee, EmployeeAdvance
from apps.payroll.services import record_employee_debt_repayment
from apps.purchasing.models import Supplier
from apps.purchasing.services import record_supplier_payment

TREASURY_VOUCHER_AUDIT_ACTION = "treasury.voucher"


def recent_treasury_voucher_logs(*, limit: int = 10):
    """آخر سندات موحّدة مسجّلة في سجل التدقيق."""
    return (
        AuditLog.objects.filter(action=TREASURY_VOUCHER_AUDIT_ACTION)
        .select_related("user")
        .order_by("-created_at")[:limit]
    )


def treasury_voucher_form_initial_from_audit(*, audit_log: AuditLog) -> dict:
    """
    يبني قيماً أولية لـ TreasuryVoucherForm من payload سجل سند موحّد (للتعديل).
    يرفع ValueError إذا كان السند ملغى أو غير مدعوم.
    """
    payload = audit_log.payload or {}
    if payload.get("cancelled"):
        raise ValueError("CANCELLED")
    vt = (payload.get("voucher_type") or "").strip()
    pt = (payload.get("party_type") or "").strip()
    if vt not in ("receipt", "disbursement") or not pt:
        raise ValueError("UNSUPPORTED_EDIT")
    if pt in ("discount_earned", "discount_allowed"):
        raise ValueError("UNSUPPORTED_EDIT")

    codes = set(get_payment_method_codes())
    method = (payload.get("method") or "").strip().lower()
    if method not in codes:
        if codes:
            method = "cash" if "cash" in codes else sorted(codes)[0]
        else:
            method = ""

    initial: dict = {
        "voucher_type": vt,
        "party_type": pt,
        "amount": Decimal(str(payload.get("amount") or "0")),
        "method": method,
        "note": (payload.get("note") or "")[:2000],
        "payer_name": (payload.get("payer_name") or "").strip()[:120],
        "payer_phone": (payload.get("payer_phone") or "").strip()[:40],
    }
    vd = payload.get("voucher_date")
    if vd:
        initial["voucher_date"] = date.fromisoformat(str(vd).strip()[:10])

    splits = payload.get("payment_splits")
    if splits and isinstance(splits, list):
        pairs: list[list[str]] = []
        for item in splits:
            if isinstance(item, dict):
                m = str(item.get("method") or "").strip().lower()
                a = item.get("amount")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                m = str(item[0] or "").strip().lower()
                a = item[1]
            else:
                continue
            if m and m in codes:
                pairs.append([m, str(a) if a is not None else ""])
        use_splits = (vt == "receipt" and pt in ("customer", "employee")) or (
            vt == "disbursement" and pt == "supplier"
        )
        if use_splits and pairs:
            initial["payment_splits_json"] = json.dumps(pairs, ensure_ascii=False)
            initial["method"] = pairs[0][0]

    if pt == "customer":
        pk = payload.get("customer_pk")
        if pk is not None:
            c = Customer.objects.filter(pk=int(pk), is_active=True).first()
            if c:
                initial["customer"] = c
    elif pt == "supplier":
        pk = payload.get("supplier_pk")
        if pk is not None:
            s = Supplier.objects.filter(pk=int(pk), is_active=True).first()
            if s:
                initial["supplier"] = s
    elif pt == "employee":
        pk = payload.get("employee_pk")
        if pk is not None:
            e = Employee.objects.filter(pk=int(pk), is_active=True).first()
            if e:
                initial["employee"] = e

    return initial


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

    if voucher_type == "receipt" and party_type == "employee":
        emp = cleaned["employee"]
        rep = record_employee_debt_repayment(
            employee=emp,
            amount=amount,
            method=method,
            note=note or "سند قبض — سداد ذمة موظف",
            user=user,
            work_session=work_session,
            voucher_date=voucher_date,
            payment_lines=payment_lines,
        )
        split_payload = (
            [{"method": m, "amount": str(a)} for m, a in payment_lines] if payment_lines else None
        )
        _log_unified_treasury_voucher(
            user,
            {
                "voucher_type": voucher_type,
                "party_type": party_type,
                "party_label": emp.name_ar,
                "amount": str(amount),
                "method": method,
                "payment_splits": split_payload,
                "note": (note or "")[:240],
                "employee_pk": emp.pk,
                "debt_repayment_pk": rep.pk,
                "store_portion": str(rep.store_portion),
                "advance_portion": str(rep.advance_portion),
                "voucher_date": voucher_date.isoformat(),
            },
        )
        return rep

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
