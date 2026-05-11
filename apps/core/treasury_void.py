"""إلغاء سندات الصندوق الموحّدة المسجّلة في سجل التدقيق — عكس الأثر المحاسبي والأرصدة."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.accounting.models import JournalEntry
from apps.accounting.services import reverse_journal_entry
from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.core.decimalutil import as_decimal
from apps.core.models import AuditLog, log_audit
from apps.core.treasury_services import TREASURY_VOUCHER_AUDIT_ACTION
from apps.expenses.models import Expense
from apps.expenses.services import delete_expense_permanent
from apps.payroll.models import EmployeeAdvance, EmployeeDebtRepayment
from apps.payroll.services import recalc_employee_net_balance
from apps.purchasing.models import SupplierLedgerEntry, SupplierPayment


def _int_payload(payload: dict, key: str) -> int:
    try:
        return int(payload[key])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError("BAD_PAYLOAD") from e


def _reverse_journals(*, reference_type: str, reference_pk: str, user, reason: str) -> None:
    for je in JournalEntry.objects.filter(
        reference_type=reference_type,
        reference_pk=str(reference_pk),
        is_reversed=False,
    ):
        reverse_journal_entry(original=je, reason=reason, user=user)


@transaction.atomic
def void_unified_treasury_voucher(*, audit_log_id: int, user) -> None:
    """
    يلغي سنداً موحّداً (قبض/صرف) اعتماداً على سجل التدقيق الأصلي.
    يحدّث payload السجل بـ cancelled ولا يحذف السجل نفسه (أثر تدقيقي).
    """
    log = AuditLog.objects.select_for_update().get(pk=audit_log_id, action=TREASURY_VOUCHER_AUDIT_ACTION)
    payload = dict(log.payload or {})
    if payload.get("cancelled"):
        raise ValueError("ALREADY_VOIDED")

    vt = (payload.get("voucher_type") or "").strip()
    party = (payload.get("party_type") or "").strip()
    reason = "إلغاء سند صندوق"

    if vt == "receipt" and party == "customer":
        le_pk = _int_payload(payload, "ledger_entry_pk")
        cust_pk = _int_payload(payload, "customer_pk")
        entry = get_object_or_404(CustomerLedgerEntry, pk=le_pk, customer_id=cust_pk)
        if entry.entry_type != CustomerLedgerEntry.EntryType.PAYMENT:
            raise ValueError("INVALID_LEDGER_ENTRY")
        _reverse_journals(
            reference_type="contacts.CustomerLedgerEntry",
            reference_pk=str(entry.pk),
            user=user,
            reason=reason,
        )
        entry.delete()
        cust = Customer.objects.get(pk=cust_pk)
        cust.balance = cust.computed_balance
        cust.save(update_fields=["balance", "updated_at"])

    elif vt == "receipt" and party == "employee":
        rep_pk = _int_payload(payload, "debt_repayment_pk")
        rep = EmployeeDebtRepayment.objects.select_for_update().select_related("employee").get(pk=rep_pk)
        emp = rep.employee
        emp.store_purchases_balance = (
            as_decimal(emp.store_purchases_balance) + as_decimal(rep.store_portion)
        ).quantize(Decimal("0.01"))
        emp.advance_balance = (as_decimal(emp.advance_balance) + as_decimal(rep.advance_portion)).quantize(
            Decimal("0.01")
        )
        emp.save(update_fields=["store_purchases_balance", "advance_balance", "updated_at"])
        recalc_employee_net_balance(emp)
        _reverse_journals(
            reference_type="payroll.EmployeeDebtRepayment",
            reference_pk=str(rep_pk),
            user=user,
            reason=reason,
        )
        rep.delete()

    elif vt == "disbursement" and party == "supplier":
        sp_pk = _int_payload(payload, "supplier_payment_pk")
        sp = SupplierPayment.objects.select_for_update().select_related("supplier").get(pk=sp_pk)
        sup = sp.supplier
        _reverse_journals(
            reference_type="purchasing.SupplierPayment",
            reference_pk=str(sp_pk),
            user=user,
            reason=reason,
        )
        SupplierLedgerEntry.objects.filter(
            reference_model="purchasing.SupplierPayment",
            reference_pk=str(sp_pk),
        ).delete()
        sp.delete()
        sup.balance = sup.computed_balance
        sup.save(update_fields=["balance", "updated_at"])

    elif vt == "disbursement" and party == "employee":
        exp_pk = _int_payload(payload, "expense_pk")
        exp = Expense.objects.select_for_update().get(pk=exp_pk)
        adv = EmployeeAdvance.objects.filter(linked_expense_id=exp_pk).select_for_update().first()
        if adv:
            emp = adv.employee
            emp.advance_balance = (as_decimal(emp.advance_balance) - as_decimal(adv.amount)).quantize(
                Decimal("0.01")
            )
            emp.save(update_fields=["advance_balance", "updated_at"])
            adv.delete()
            recalc_employee_net_balance(emp)
        delete_expense_permanent(expense=exp, user=user)

    elif vt == "disbursement" and party == "expense":
        exp_pk = _int_payload(payload, "expense_pk")
        exp = Expense.objects.get(pk=exp_pk)
        delete_expense_permanent(expense=exp, user=user)

    elif (vt == "receipt" and party == "discount_earned") or (vt == "disbursement" and party == "discount_allowed"):
        je_pk = _int_payload(payload, "journal_entry_pk")
        try:
            je = JournalEntry.objects.select_for_update().get(pk=je_pk)
        except JournalEntry.DoesNotExist as e:
            raise ValueError("BAD_PAYLOAD") from e
        try:
            reverse_journal_entry(original=je, reason=reason, user=user)
        except ValueError as e:
            if str(e) == "ALREADY_REVERSED":
                raise ValueError("ALREADY_REVERSED") from e
            raise

    else:
        raise ValueError("UNKNOWN_TREASURY_VOUCHER")

    payload["cancelled"] = True
    payload["voided_at"] = timezone.now().isoformat()
    if user and user.is_authenticated:
        payload["voided_by_user_id"] = user.pk
    log.payload = payload
    log.save(update_fields=["payload"])

    log_audit(
        user,
        "treasury.voucher_voided",
        "treasury.UnifiedVoucher",
        str(audit_log_id),
        {"original_action": TREASURY_VOUCHER_AUDIT_ACTION, "voucher_type": vt, "party_type": party},
    )
