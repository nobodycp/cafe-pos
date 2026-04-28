"""إلغاء فواتير البيع مع عكس القيود المحاسبية."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.billing.models import InvoicePayment, SaleInvoice
from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.core.models import log_audit
from apps.inventory.services import adjust_stock
from apps.inventory.models import StockMovement


def _d(v) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


@transaction.atomic
def cancel_sale_invoice(*, invoice: SaleInvoice, reason: str, user) -> None:
    """
    إلغاء فاتورة بيع:
    1. تعليم الفاتورة كملغاة (soft cancel)
    2. عكس القيود المحاسبية
    3. إعادة المخزون
    4. عكس قيد العميل الائتماني إن وجد
    """
    if invoice.is_cancelled:
        raise ValueError("ALREADY_CANCELLED")
    if invoice.returns.exists():
        raise ValueError("INVOICE_HAS_RETURNS")

    invoice.soft_cancel(reason)

    from apps.accounting.models import JournalEntry
    from apps.accounting.services import reverse_journal_entry

    journal_entries = JournalEntry.objects.filter(
        reference_type="billing.SaleInvoice",
        reference_pk=str(invoice.pk),
        is_reversed=False,
    )
    for je in journal_entries:
        reverse_journal_entry(original=je, reason=f"إلغاء فاتورة {invoice.invoice_number}: {reason}", user=user)

    movements = StockMovement.objects.filter(
        reference_model="billing.SaleInvoice",
        reference_pk=str(invoice.pk),
    )
    for mv in movements.select_related("product"):
        reversed_delta = -_d(mv.quantity_delta)
        adjust_stock(
            product=mv.product,
            quantity_delta=reversed_delta,
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            session=invoice.work_session,
            reference_model="billing.SaleInvoice",
            reference_pk=str(invoice.pk),
            note=f"عكس حركة إلغاء فاتورة {invoice.invoice_number}",
        )

    credit_payments = invoice.payments.filter(method=InvoicePayment.Method.CREDIT)
    credit_total = sum(_d(p.amount) for p in credit_payments)
    if credit_total > 0 and invoice.customer:
        cust = invoice.customer
        cust.balance = (_d(cust.balance) - credit_total).quantize(Decimal("0.01"))
        if cust.balance < 0 and cust.balance > Decimal("-0.01"):
            cust.balance = Decimal("0")
        cust.save(update_fields=["balance", "updated_at"])
        CustomerLedgerEntry.objects.create(
            customer=cust,
            entry_type=CustomerLedgerEntry.EntryType.ADJUSTMENT,
            amount=-credit_total,
            note=f"إلغاء فاتورة {invoice.invoice_number}: {reason}",
            reference_model="billing.SaleInvoice",
            reference_pk=str(invoice.pk),
        )
        linked = getattr(cust, "linked_supplier", None)
        if linked:
            from apps.purchasing.models import SupplierLedgerEntry

            linked.balance = (_d(linked.balance) + credit_total).quantize(Decimal("0.01"))
            linked.save(update_fields=["balance", "updated_at"])
            SupplierLedgerEntry.objects.create(
                supplier=linked,
                entry_type=SupplierLedgerEntry.EntryType.ADJUSTMENT,
                amount=credit_total,
                note=f"عكس مشتريات العميل — إلغاء فاتورة {invoice.invoice_number}",
                reference_model="billing.SaleInvoice",
                reference_pk=str(invoice.pk),
            )

    from apps.purchasing.models import Supplier, SupplierLedgerEntry

    vendor_totals: dict = {}
    for sil in invoice.lines.select_related("product"):
        p = sil.product
        if p.product_type != p.ProductType.COMMISSION or not p.commission_vendor_id:
            continue
        pct = _d(p.commission_percentage or 0)
        vendor_payable = (sil.line_subtotal - sil.line_subtotal * pct / Decimal("100")).quantize(Decimal("0.01"))
        vendor_totals[p.commission_vendor_id] = vendor_totals.get(p.commission_vendor_id, Decimal("0")) + vendor_payable

    for vendor_id, total in vendor_totals.items():
        if total <= 0:
            continue
        supplier = Supplier.objects.select_for_update().get(pk=vendor_id)
        supplier.balance = (_d(supplier.balance) - total).quantize(Decimal("0.01"))
        if supplier.balance < 0 and supplier.balance > Decimal("-0.01"):
            supplier.balance = Decimal("0")
        supplier.save(update_fields=["balance", "updated_at"])
        SupplierLedgerEntry.objects.create(
            supplier=supplier,
            entry_type=SupplierLedgerEntry.EntryType.ADJUSTMENT,
            amount=-total,
            note=f"عكس مستحقات بائع نسبة — إلغاء فاتورة {invoice.invoice_number}",
            reference_model="billing.SaleInvoice",
            reference_pk=str(invoice.pk),
        )

    log_audit(
        user,
        "sale.invoice.cancelled",
        "billing.SaleInvoice",
        invoice.pk,
        {"reason": reason, "total": str(invoice.total)},
    )
