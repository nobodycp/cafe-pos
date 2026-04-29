"""حذف فاتورة شراء نهائياً مع إزالة آثارها المالية والمخزنية."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db import transaction

from apps.accounting.models import JournalEntry
from apps.core.models import log_audit
from apps.inventory.models import StockBalance, StockMovement
from apps.purchasing.models import PurchaseInvoice, PurchaseReturn, SupplierLedgerEntry, SupplierPayment


@transaction.atomic
def purge_purchase_invoice(*, invoice: PurchaseInvoice, reason: str = "", user=None) -> None:
    supplier = invoice.supplier
    pk = str(invoice.pk)
    inv_number = invoice.invoice_number
    returns = list(PurchaseReturn.objects.filter(purchase_invoice=invoice))
    return_pks = [str(r.pk) for r in returns]

    stock_refs = [("purchasing.PurchaseInvoice", pk)]
    stock_refs += [("purchasing.PurchaseReturn", rpk) for rpk in return_pks]

    qty_by_product: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for ref_model, ref_pk in stock_refs:
        for movement in StockMovement.objects.select_related("product").filter(
            reference_model=ref_model,
            reference_pk=ref_pk,
        ):
            qty_by_product[movement.product_id] += movement.quantity_delta

    for product_id, qty_delta in qty_by_product.items():
        balance, _ = StockBalance.objects.select_for_update().get_or_create(
            product_id=product_id,
            defaults={"quantity_on_hand": Decimal("0"), "average_cost": Decimal("0")},
        )
        balance.quantity_on_hand = (balance.quantity_on_hand - qty_delta).quantize(Decimal("0.0001"))
        balance.save(update_fields=["quantity_on_hand", "updated_at"])

    StockMovement.objects.filter(reference_model="purchasing.PurchaseInvoice", reference_pk=pk).delete()
    if return_pks:
        StockMovement.objects.filter(reference_model="purchasing.PurchaseReturn", reference_pk__in=return_pks).delete()

    SupplierLedgerEntry.objects.filter(reference_model="purchasing.PurchaseInvoice", reference_pk=pk).delete()
    if return_pks:
        SupplierLedgerEntry.objects.filter(reference_model="purchasing.PurchaseReturn", reference_pk__in=return_pks).delete()

    SupplierPayment.objects.filter(supplier=supplier, note=f"سداد فاتورة {inv_number}").delete()

    refs = JournalEntry.objects.filter(reference_type="purchasing.PurchaseInvoice", reference_pk=pk)
    JournalEntry.objects.filter(reversed_by__in=refs).update(reversed_by=None, is_reversed=False)
    refs.delete()

    inv_pk = invoice.pk
    for ret in returns:
        ret.delete()
    invoice.delete()

    supplier.balance = supplier.computed_balance
    supplier.save(update_fields=["balance", "updated_at"])

    log_audit(
        user,
        "purchase.invoice.purged",
        "purchasing.PurchaseInvoice",
        inv_pk,
        {"reason": reason, "invoice_number": inv_number},
    )
