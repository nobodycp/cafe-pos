"""حذف فاتورة بيع نهائياً من النظام بعد عكس الأثر المالي والمخزوني."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.accounting.models import JournalEntry
from apps.billing.cancel_service import cancel_sale_invoice
from apps.billing.models import SaleInvoice, SaleReturn
from apps.contacts.models import CustomerLedgerEntry
from apps.core.decimalutil import as_decimal
from apps.core.models import log_audit
from apps.inventory.models import StockMovement
from apps.inventory.services import adjust_stock
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


@transaction.atomic
def purge_sale_return(*, sale_return: SaleReturn, reason: str, user) -> None:
    """
    يعكس أثر مرتجع البيع (حركات المخزون ورصيد العميل عند الاسترداد كرصيد)،
    ثم يحذف المرتجع وأسطره. يُستخدم لإزالة سجلات خاطئة أو بقايا ترحيل.
    """
    ret = (
        SaleReturn.objects.select_for_update()
        .select_related("invoice", "invoice__customer")
        .prefetch_related("lines__product")
        .get(pk=sale_return.pk)
    )
    invoice = ret.invoice
    session = invoice.work_session
    ref_pk = str(ret.pk)
    ret_no = ret.return_number

    mvs = list(
        StockMovement.objects.filter(
            reference_model="billing.SaleReturn",
            reference_pk=ref_pk,
        ).select_related("product")
    )
    for mv in mvs:
        rev = -as_decimal(mv.quantity_delta)
        if rev == 0:
            continue
        adjust_stock(
            product=mv.product,
            quantity_delta=rev,
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            session=session,
            reference_model="billing.SaleReturn",
            reference_pk=ref_pk,
            note=f"عكس حذف مرتجع {ret_no}",
        )

    StockMovement.objects.filter(reference_model="billing.SaleReturn", reference_pk=ref_pk).delete()

    if not mvs:
        for line in ret.lines.select_related("product"):
            p = line.product
            if not p.is_stock_tracked:
                continue
            qty = as_decimal(line.quantity)
            if qty == 0:
                continue
            adjust_stock(
                product=p,
                quantity_delta=-qty,
                movement_type=StockMovement.MovementType.ADJUSTMENT,
                session=session,
                reference_model="billing.SaleReturn",
                reference_pk=ref_pk,
                note=f"عكس مرتجع (بدون حركات مخزون مسجّلة) {ret_no}",
            )
        StockMovement.objects.filter(reference_model="billing.SaleReturn", reference_pk=ref_pk).delete()

    if ret.refund_method == "credit" and invoice.customer_id:
        CustomerLedgerEntry.objects.filter(
            reference_model="billing.SaleReturn",
            reference_pk=ref_pk,
        ).delete()
        cust = invoice.customer
        tot = as_decimal(ret.total_refund)
        cust.balance = (as_decimal(cust.balance) + tot).quantize(Decimal("0.01"))
        cust.save(update_fields=["balance", "updated_at"])

    inv_pk = invoice.pk
    ret.delete()

    log_audit(
        user,
        "sale.return.purged",
        "billing.SaleInvoice",
        inv_pk,
        {"reason": reason, "return_number": ret_no},
    )
