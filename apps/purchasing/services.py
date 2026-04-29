from __future__ import annotations

from decimal import Decimal
from typing import List, Tuple

from django.db import transaction

from apps.catalog.models import Product
from apps.core.models import log_audit
from apps.core.payment_methods import credit_method_codes, get_payment_method_codes, resolve_ledger_account_code
from apps.core.sequences import next_int
from apps.core.services import SessionService
from apps.inventory.services import receive_purchase_stock
from apps.core.decimalutil import as_decimal
from apps.purchasing.models import PurchaseInvoice, PurchaseLine, Supplier, SupplierLedgerEntry, SupplierPayment


def next_purchase_invoice_number() -> str:
    return f"PUR-{next_int('purchase_invoice'):06d}"


@transaction.atomic
def post_purchase_invoice(
    *,
    supplier: Supplier,
    lines: List[Tuple[Product, Decimal, Decimal]],
    user,
    payments: List[Tuple[str, Decimal]],
    work_session=None,
) -> PurchaseInvoice:
    """
    lines: (Product, quantity, unit_cost)
    payments: (method, amount) with method cash|bank|credit — amounts must sum to invoice total.
    """
    session = work_session or SessionService.get_open_session()
    total = Decimal("0")
    pur = PurchaseInvoice.objects.create(
        invoice_number=next_purchase_invoice_number(),
        supplier=supplier,
        work_session=session,
        total=0,
        payment_status=PurchaseInvoice.PaymentStatus.PAID,
    )
    for product, qty, unit_cost in lines:
        lt = (as_decimal(qty) * as_decimal(unit_cost)).quantize(Decimal("0.01"))
        total += lt
        PurchaseLine.objects.create(
            purchase=pur,
            product=product,
            quantity=as_decimal(qty),
            unit_cost=as_decimal(unit_cost),
            line_total=lt,
        )
        if product.is_stock_tracked:
            receive_purchase_stock(
                product=product,
                quantity=as_decimal(qty),
                unit_cost=as_decimal(unit_cost),
                session=session,
                reference_model="purchasing.PurchaseInvoice",
                reference_pk=str(pur.pk),
            )

    pur.total = total
    pur.save(update_fields=["total", "updated_at"])

    pay_sum = sum((as_decimal(a) for _, a in payments), Decimal("0")).quantize(Decimal("0.01"))
    if pay_sum != total:
        raise ValueError("PURCHASE_PAYMENT_SUM_MISMATCH")

    supplier.balance = (supplier.balance + total).quantize(Decimal("0.01"))
    supplier.save(update_fields=["balance", "updated_at"])
    SupplierLedgerEntry.objects.create(
        supplier=supplier,
        entry_type=SupplierLedgerEntry.EntryType.PURCHASE,
        amount=total,
        note=f"فاتورة شراء {pur.invoice_number}",
        reference_model="purchasing.PurchaseInvoice",
        reference_pk=str(pur.pk),
    )

    for method, amount in payments:
        amt = as_decimal(amount)
        if amt <= 0:
            continue
        if method == "credit":
            continue
        supplier.balance = (supplier.balance - amt).quantize(Decimal("0.01"))
        supplier.save(update_fields=["balance", "updated_at"])
        SupplierPayment.objects.create(
            supplier=supplier,
            work_session=session,
            amount=amt,
            method=str(method),
            note=f"سداد فاتورة {pur.invoice_number}",
        )
        SupplierLedgerEntry.objects.create(
            supplier=supplier,
            entry_type=SupplierLedgerEntry.EntryType.PAYMENT,
            amount=-amt,
            note=f"سداد فاتورة {pur.invoice_number}",
            reference_model="purchasing.PurchaseInvoice",
            reference_pk=str(pur.pk),
        )

    ar_codes = credit_method_codes()
    pay_pos = [(m, a) for m, a in payments if as_decimal(a) > 0]
    credit_only = bool(pay_pos) and all(m in ar_codes for m, _ in pay_pos)
    if credit_only and total > 0:
        pur.payment_status = PurchaseInvoice.PaymentStatus.UNPAID
    elif any(m in ar_codes for m, _ in pay_pos) and any(m not in ar_codes for m, _ in pay_pos):
        pur.payment_status = PurchaseInvoice.PaymentStatus.PARTIAL
    else:
        pur.payment_status = PurchaseInvoice.PaymentStatus.PAID
    pur.save(update_fields=["payment_status", "updated_at"])

    from apps.accounting.services import post_purchase_invoice_journal

    pur_pay_map = {"cash": Decimal("0"), "bank": Decimal("0"), "credit": Decimal("0")}
    for method, amount in payments:
        m = str(method)
        amt = as_decimal(amount)
        if amt <= 0:
            continue
        sys = resolve_ledger_account_code(m)
        if sys == "AR":
            pur_pay_map["credit"] += amt
        elif sys == "CASH":
            pur_pay_map["cash"] += amt
        else:
            pur_pay_map["bank"] += amt
    post_purchase_invoice_journal(purchase_invoice=pur, pay_by_method=pur_pay_map, user=user)

    log_audit(user, "purchase.post", "purchasing.PurchaseInvoice", pur.pk, {"total": str(total)})
    return pur


@transaction.atomic
def record_supplier_payment(
    *,
    supplier: Supplier,
    amount: Decimal,
    method: str,
    user,
    note: str = "",
    work_session=None,
) -> SupplierPayment:
    """سداد مستقل لمورد (خارج سياق فاتورة شراء)."""
    amt = as_decimal(amount)
    if amt <= 0:
        raise ValueError("INVALID_AMOUNT")

    session = work_session or SessionService.get_open_session()
    supplier.balance = (supplier.balance - amt).quantize(Decimal("0.01"))
    supplier.save(update_fields=["balance", "updated_at"])

    m = str(method or "").strip().lower()
    if m not in get_payment_method_codes():
        raise ValueError("INVALID_PAYMENT_METHOD")

    sp = SupplierPayment.objects.create(
        supplier=supplier,
        work_session=session,
        amount=amt,
        method=m,
        note=note or "سداد مستقل",
    )
    SupplierLedgerEntry.objects.create(
        supplier=supplier,
        entry_type=SupplierLedgerEntry.EntryType.PAYMENT,
        amount=-amt,
        note=note or "سداد مستقل",
        reference_model="purchasing.SupplierPayment",
        reference_pk=str(sp.pk),
    )

    from apps.accounting.services import post_supplier_payment_journal

    post_supplier_payment_journal(
        supplier=supplier,
        amount=amt,
        method=method,
        reference_type="purchasing.SupplierPayment",
        reference_pk=str(sp.pk),
        work_session=session,
        user=user,
    )

    log_audit(user, "supplier.payment", "purchasing.Supplier", supplier.pk, {"amount": str(amt)})
    return sp
