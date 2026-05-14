"""ربط فواتير البيع بالآجل بحساب الموظف عند وجود عميل مرتبط."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.billing.models import SaleInvoice
from apps.contacts.models import Customer
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
    EmployeeCafePurchase.objects.create(
        employee=emp,
        work_session=work_session,
        amount=amt,
        note=note,
        sale_invoice=invoice,
    )
    emp.store_purchases_balance = (as_decimal(emp.store_purchases_balance) + amt).quantize(Decimal("0.01"))
    emp.save(update_fields=["store_purchases_balance", "updated_at"])
    recalc_employee_net_balance(emp)


@transaction.atomic
def reverse_employee_cafe_for_cancelled_invoice(*, invoice: SaleInvoice) -> None:
    """عند إلغاء فاتورة: عكس سجل المقهى المرتبط بها إن وُجد."""
    for cp in EmployeeCafePurchase.objects.filter(sale_invoice_id=invoice.pk).select_related("employee"):
        emp = cp.employee
        emp.store_purchases_balance = (as_decimal(emp.store_purchases_balance) - as_decimal(cp.amount)).quantize(
            Decimal("0.01")
        )
        if emp.store_purchases_balance < 0 and emp.store_purchases_balance > Decimal("-0.01"):
            emp.store_purchases_balance = Decimal("0")
        emp.save(update_fields=["store_purchases_balance", "updated_at"])
        cp.delete()
        recalc_employee_net_balance(emp)
