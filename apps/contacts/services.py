from decimal import Decimal

from django.db import transaction

from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.core.decimalutil import as_decimal
from apps.core.models import log_audit


@transaction.atomic
def record_customer_payment(
    *, customer: Customer, amount: Decimal, user, method: str = "cash", note: str = "",
    work_session=None,
) -> CustomerLedgerEntry:
    amt = as_decimal(amount)
    if amt <= 0:
        raise ValueError("INVALID_AMOUNT")
    customer.balance = (customer.balance - amt).quantize(Decimal("0.01"))
    if customer.balance < 0 and customer.balance > Decimal("-0.01"):
        customer.balance = Decimal("0")
    customer.save(update_fields=["balance", "updated_at"])
    entry = CustomerLedgerEntry.objects.create(
        customer=customer,
        entry_type=CustomerLedgerEntry.EntryType.PAYMENT,
        amount=-amt,
        note=note or "سداد",
    )

    from apps.accounting.services import post_customer_payment_journal

    post_customer_payment_journal(
        customer=customer,
        amount=amt,
        method=method,
        reference_type="contacts.CustomerLedgerEntry",
        reference_pk=str(entry.pk),
        work_session=work_session,
        user=user,
    )

    log_audit(user, "customer.payment", "contacts.Customer", customer.pk, {"amount": str(amt)})
    return entry
