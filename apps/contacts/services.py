from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple

from django.db import transaction

from apps.contacts.models import Customer, CustomerLedgerEntry


def resolve_or_create_active_customer_by_name(name_ar: str) -> Tuple[Optional[Customer], bool]:
    """
    يبحث عن عميل نشط بالاسم (بدون حساسية لحالة الأحرف) أو ينشئ عميلاً جديداً.
    يعيد (العميل، True إن كان موجوداً مسبقاً) أو (None, False) إن كان الاسم قصيراً جداً.
    """
    raw = (name_ar or "").strip()[:200]
    if len(raw) < 2:
        return None, False
    c = Customer.objects.filter(name_ar__iexact=raw, is_active=True).first()
    if c:
        return c, True
    c = Customer.objects.create(name_ar=raw, name_en="", phone="")
    return c, False
from apps.core.decimalutil import as_decimal
from apps.core.models import log_audit
from apps.core.payment_methods import resolve_ledger_account_code


@transaction.atomic
def record_customer_payment(
    *,
    customer: Customer,
    amount: Decimal,
    user,
    method: str = "cash",
    note: str = "",
    work_session=None,
    payer_name: str = "",
    payer_phone: str = "",
    payment_lines: Optional[List[Tuple[str, Decimal]]] = None,
    entry_date: Optional[date] = None,
) -> CustomerLedgerEntry:
    amt = as_decimal(amount)
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

    customer.balance = (customer.balance - pay_collected).quantize(Decimal("0.01"))
    if customer.balance < 0 and customer.balance > Decimal("-0.01"):
        customer.balance = Decimal("0")
    customer.save(update_fields=["balance", "updated_at"])
    base = (note or "").strip() or "سداد"
    if entry_date:
        base = f"{base} · {entry_date.isoformat()}"
    pn = (payer_name or "").strip()[:120]
    ph = (payer_phone or "").strip()[:40]
    if pn or ph:
        bits = []
        if pn:
            bits.append(f"المحوّل: {pn}")
        if ph:
            bits.append(f"جوال: {ph}")
        base = f"{base} — " + " · ".join(bits)
    if len(lines) > 1:
        split_txt = " · ".join(f"{m}:{a}" for m, a in lines)
        base = f"{base} — [{split_txt}]" if base else f"[{split_txt}]"
    entry = CustomerLedgerEntry.objects.create(
        customer=customer,
        entry_type=CustomerLedgerEntry.EntryType.PAYMENT,
        amount=-pay_collected,
        note=base,
    )

    from apps.accounting.services import post_customer_payment_journal, post_customer_payment_journal_multi

    use_multi = len(lines) > 1 or any(
        resolve_ledger_account_code(m) == "AR" and a > 0 for m, a in lines
    )
    if not use_multi:
        post_customer_payment_journal(
            customer=customer,
            amount=lines[0][1],
            method=lines[0][0],
            reference_type="contacts.CustomerLedgerEntry",
            reference_pk=str(entry.pk),
            work_session=work_session,
            user=user,
            entry_date=entry_date,
        )
    else:
        post_customer_payment_journal_multi(
            customer=customer,
            payments=lines,
            reference_type="contacts.CustomerLedgerEntry",
            reference_pk=str(entry.pk),
            work_session=work_session,
            user=user,
            entry_date=entry_date,
        )

    log_audit(user, "customer.payment", "contacts.Customer", customer.pk, {"amount": str(pay_collected)})
    return entry
