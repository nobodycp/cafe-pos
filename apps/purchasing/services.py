from __future__ import annotations

from decimal import Decimal
from typing import List, Tuple

from django.db import transaction

from apps.catalog.models import Product
from apps.core.models import log_audit
from apps.core.sequences import next_int
from apps.core.services import SessionService
from apps.inventory.services import receive_purchase_stock
from apps.purchasing.models import PurchaseInvoice, PurchaseLine, Supplier, SupplierLedgerEntry, SupplierPayment


def _d(v) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


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
        lt = (_d(qty) * _d(unit_cost)).quantize(Decimal("0.01"))
        total += lt
        PurchaseLine.objects.create(
            purchase=pur,
            product=product,
            quantity=_d(qty),
            unit_cost=_d(unit_cost),
            line_total=lt,
        )
        if product.is_stock_tracked:
            receive_purchase_stock(
                product=product,
                quantity=_d(qty),
                unit_cost=_d(unit_cost),
                session=session,
                reference_model="purchasing.PurchaseInvoice",
                reference_pk=str(pur.pk),
            )

    pur.total = total
    pur.save(update_fields=["total", "updated_at"])

    pay_sum = sum((_d(a) for _, a in payments), Decimal("0")).quantize(Decimal("0.01"))
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
        amt = _d(amount)
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
            method=SupplierPayment.Method.CASH if method == "cash" else SupplierPayment.Method.BANK,
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

    credit_only = all(m == "credit" for m, a in payments if _d(a) > 0)
    if credit_only and total > 0:
        pur.payment_status = PurchaseInvoice.PaymentStatus.UNPAID
    elif any(m == "credit" for m, a in payments if _d(a) > 0) and any(
        m in ("cash", "bank") for m, a in payments if _d(a) > 0
    ):
        pur.payment_status = PurchaseInvoice.PaymentStatus.PARTIAL
    else:
        pur.payment_status = PurchaseInvoice.PaymentStatus.PAID
    pur.save(update_fields=["payment_status", "updated_at"])

    from apps.accounting.services import post_purchase_invoice_journal

    pur_pay_map = {"cash": Decimal("0"), "bank": Decimal("0"), "credit": Decimal("0")}
    for method, amount in payments:
        m = str(method)
        if m in pur_pay_map:
            pur_pay_map[m] += _d(amount)
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
    amt = _d(amount)
    if amt <= 0:
        raise ValueError("INVALID_AMOUNT")

    session = work_session or SessionService.get_open_session()
    supplier.balance = (supplier.balance - amt).quantize(Decimal("0.01"))
    supplier.save(update_fields=["balance", "updated_at"])

    sp = SupplierPayment.objects.create(
        supplier=supplier,
        work_session=session,
        amount=amt,
        method=SupplierPayment.Method.CASH if method == "cash" else SupplierPayment.Method.BANK,
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
