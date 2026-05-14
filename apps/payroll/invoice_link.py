"""ربط فواتير البيع بالآجل بحساب الموظف عند وجود عميل مرتبط."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.billing.models import SaleInvoice
from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.core.decimalutil import as_decimal
from apps.core.models import WorkSession
from apps.payroll.models import Employee, EmployeeCafePurchase
from apps.payroll.services import recalc_employee_net_balance


@transaction.atomic
def maybe_record_employee_cafe_from_invoice_credit(
    *,
    invoice: SaleInvoice,
    customer: Customer,
    credit_total: Decimal,
    work_session: WorkSession,
) -> None:
    """
    عند إنشاء فاتورة بيع بمبلغ آجل على عميل مرتبط بموظف:
    يُنشئ سجل EmployeeCafePurchase ويرفع store_purchases_balance بنفس المبلغ.
    """
    amt = as_decimal(credit_total).quantize(Decimal("0.01"))
    if amt <= 0:
        return
    emp = (
        Employee.objects.select_for_update()
        .filter(linked_customer_id=customer.pk, is_active=True)
        .first()
    )
    if not emp:
        return
    if EmployeeCafePurchase.objects.filter(sale_invoice_id=invoice.pk).exists():
        return
    note = f"فاتورة {invoice.invoice_number} (آجل)"
    cp = EmployeeCafePurchase.objects.create(
        employee=emp,
        work_session=work_session,
        amount=amt,
        note=note,
        sale_invoice=invoice,
    )
    emp.store_purchases_balance = (as_decimal(emp.store_purchases_balance) + amt).quantize(Decimal("0.01"))
    emp.save(update_fields=["store_purchases_balance", "updated_at"])
    recalc_employee_net_balance(emp)

    # نفس المبلغ كان أُضيف لرصيد العميل مع قيد الفاتورة؛ نصفّره على العميل لأن الذمة تُتابع على بطاقة الموظف.
    cust = Customer.objects.select_for_update().get(pk=customer.pk)
    cust.balance = (as_decimal(cust.balance) - amt).quantize(Decimal("0.01"))
    if cust.balance < 0 and cust.balance > Decimal("-0.01"):
        cust.balance = Decimal("0")
    cust.save(update_fields=["balance", "updated_at"])
    CustomerLedgerEntry.objects.create(
        customer=cust,
        entry_type=CustomerLedgerEntry.EntryType.ADJUSTMENT,
        amount=-amt,
        note=f"ترحيل ذمة آجل إلى مشتريات مقهى الموظف — {invoice.invoice_number}",
        reference_model="payroll.EmployeeCafePurchase",
        reference_pk=str(cp.pk),
    )


@transaction.atomic
def reverse_employee_cafe_for_cancelled_invoice(*, invoice: SaleInvoice) -> None:
    """عند إلغاء فاتورة أو عكس آجلها: عكس سجل المقهى المرتبط بها إن وُجد."""
    for cp in EmployeeCafePurchase.objects.filter(sale_invoice_id=invoice.pk).select_related("employee", "sale_invoice"):
        emp = cp.employee
        cust_id = None
        if cp.sale_invoice_id and cp.sale_invoice.customer_id:
            cust_id = cp.sale_invoice.customer_id
        elif emp.linked_customer_id:
            cust_id = emp.linked_customer_id
        if cust_id:
            CustomerLedgerEntry.objects.filter(
                customer_id=cust_id,
                reference_model="payroll.EmployeeCafePurchase",
                reference_pk=str(cp.pk),
            ).delete()
            cust = Customer.objects.select_for_update().get(pk=cust_id)
            cust.balance = cust.computed_balance
            cust.save(update_fields=["balance", "updated_at"])
        emp.store_purchases_balance = (as_decimal(emp.store_purchases_balance) - as_decimal(cp.amount)).quantize(
            Decimal("0.01")
        )
        if emp.store_purchases_balance < 0 and emp.store_purchases_balance > Decimal("-0.01"):
            emp.store_purchases_balance = Decimal("0")
        emp.save(update_fields=["store_purchases_balance", "updated_at"])
        cp.delete()
        recalc_employee_net_balance(emp)
