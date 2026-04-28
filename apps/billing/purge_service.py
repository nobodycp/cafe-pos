"""حذف فاتورة بيع نهائياً من النظام بعد عكس الأثر المالي والمخزوني."""
from __future__ import annotations

from django.db import transaction

from apps.accounting.models import JournalEntry
from apps.billing.cancel_service import cancel_sale_invoice
from apps.billing.models import SaleInvoice
from apps.contacts.models import CustomerLedgerEntry
from apps.core.models import log_audit
from apps.inventory.models import StockMovement
from apps.purchasing.models import SupplierLedgerEntry


@transaction.atomic
def purge_sale_invoice(*, invoice: SaleInvoice, reason: str, user) -> None:
    """
    يعكس أثر الفاتورة (مثل الإلغاء) ثم يحذف السجلات والقيود والحركات المرتبطة.
    لا يُنفَّذ إن وُجد مرتجع بيع على الفاتورة.
    """
    if invoice.returns.exists():
        raise ValueError("INVOICE_HAS_RETURNS")

    if not invoice.is_cancelled:
        cancel_sale_invoice(invoice=invoice, reason=reason or "حذف نهائي", user=user)

    pk = str(invoice.pk)

    CustomerLedgerEntry.objects.filter(reference_model="billing.SaleInvoice", reference_pk=pk).delete()
    SupplierLedgerEntry.objects.filter(reference_model="billing.SaleInvoice", reference_pk=pk).delete()
    StockMovement.objects.filter(reference_model="billing.SaleInvoice", reference_pk=pk).delete()

    refs = JournalEntry.objects.filter(reference_type="billing.SaleInvoice", reference_pk=pk)
    JournalEntry.objects.filter(reversed_by__in=refs).update(reversed_by=None, is_reversed=False)
    refs.delete()

    invoice.source_tab_payments.update(sale_invoice=None)

    inv_pk = invoice.pk
    invoice.delete()

    log_audit(
        user,
        "sale.invoice.purged",
        "billing.SaleInvoice",
        inv_pk,
        {"reason": reason},
    )
