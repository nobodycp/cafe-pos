"""ربط فواتير البيع بالآجل بحساب المورد عند وجود عميل مرتبط (بدون تكرار مع مسار الموظف)."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.billing.models import SaleInvoice
from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.core.decimalutil import as_decimal
from apps.core.models import WorkSession
from apps.payroll.models import EmployeeCafePurchase
from apps.purchasing.models import Supplier, SupplierCafePurchase, SupplierLedgerEntry


@transaction.atomic
def maybe_record_supplier_cafe_from_invoice_credit(
    *,
    invoice: SaleInvoice,
    customer: Customer,
    credit_total: Decimal,
    work_session: WorkSession,
) -> None:
    """
    عند فاتورة آجل على عميل مرتبط بمورد (وليس بمسار موظف):
    يسجّل SupplierCafePurchase ويخصم من رصيد المورد (ذمة أقل علينا) ويصفّر رصيد العميل بقيد تسوية.
    """
    amt = as_decimal(credit_total).quantize(Decimal("0.01"))
    if amt <= 0:
        return
    if EmployeeCafePurchase.objects.filter(sale_invoice_id=invoice.pk).exists():
        return
    if SupplierCafePurchase.objects.filter(sale_invoice_id=invoice.pk).exists():
        return

    sup = (
        Supplier.objects.select_for_update()
        .filter(linked_customer_id=customer.pk, is_active=True)
        .first()
    )
    if not sup:
        return

    note = f"فاتورة {invoice.invoice_number} (آجل — مقهى)"
    scp = SupplierCafePurchase.objects.create(
        supplier=sup,
        work_session=work_session,
        amount=amt,
        note=note,
        sale_invoice=invoice,
    )
    sup.balance = (as_decimal(sup.balance) - amt).quantize(Decimal("0.01"))
    if sup.balance < 0 and sup.balance > Decimal("-0.01"):
        sup.balance = Decimal("0")
    sup.save(update_fields=["balance", "updated_at"])
    SupplierLedgerEntry.objects.create(
        supplier=sup,
        entry_type=SupplierLedgerEntry.EntryType.ADJUSTMENT,
        amount=-amt,
        note=f"مشتريات من المقهى (آجل) — {invoice.invoice_number}",
        reference_model="purchasing.SupplierCafePurchase",
        reference_pk=str(scp.pk),
    )

    cust = Customer.objects.select_for_update().get(pk=customer.pk)
    cust.balance = (as_decimal(cust.balance) - amt).quantize(Decimal("0.01"))
    if cust.balance < 0 and cust.balance > Decimal("-0.01"):
        cust.balance = Decimal("0")
    cust.save(update_fields=["balance", "updated_at"])
    CustomerLedgerEntry.objects.create(
        customer=cust,
        entry_type=CustomerLedgerEntry.EntryType.ADJUSTMENT,
        amount=-amt,
        note=f"ترحيل ذمة آجل إلى حساب المورد (مشتريات مقهى) — {invoice.invoice_number}",
        reference_model="purchasing.SupplierCafePurchase",
        reference_pk=str(scp.pk),
    )


@transaction.atomic
def reverse_supplier_cafe_for_cancelled_invoice(*, invoice: SaleInvoice) -> None:
    """عند إلغاء فاتورة أو عكس آجلها: عكس مشتريات المقهى المسجّلة للمورد المرتبط."""
    for scp in SupplierCafePurchase.objects.filter(sale_invoice_id=invoice.pk).select_related("supplier", "sale_invoice"):
        sup = scp.supplier
        cust_id = None
        if scp.sale_invoice_id and scp.sale_invoice.customer_id:
            cust_id = scp.sale_invoice.customer_id
        elif sup.linked_customer_id:
            cust_id = sup.linked_customer_id
        if cust_id:
            CustomerLedgerEntry.objects.filter(
                customer_id=cust_id,
                reference_model="purchasing.SupplierCafePurchase",
                reference_pk=str(scp.pk),
            ).delete()
            cust = Customer.objects.select_for_update().get(pk=cust_id)
            cust.balance = cust.computed_balance
            cust.save(update_fields=["balance", "updated_at"])
        SupplierLedgerEntry.objects.filter(
            supplier_id=sup.pk,
            reference_model="purchasing.SupplierCafePurchase",
            reference_pk=str(scp.pk),
        ).delete()
        sup = Supplier.objects.select_for_update().get(pk=sup.pk)
        sup.balance = sup.computed_balance
        sup.save(update_fields=["balance", "updated_at"])
        scp.delete()
