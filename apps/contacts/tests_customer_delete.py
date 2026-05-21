from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.billing.models import SaleInvoice
from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.core.models import AuditLog
from apps.core.treasury_services import TREASURY_VOUCHER_AUDIT_ACTION
from apps.payroll.models import Employee
from apps.pos.models import Order
from apps.purchasing.models import Supplier


class CustomerDeleteCascadeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cust_del", password="pass-12345")
        self.client.login(username="cust_del", password="pass-12345")

    def test_delete_customer_cleans_related_operations(self):
        customer = Customer.objects.create(name_ar="عميل حذف")
        supplier = Supplier.objects.create(name_ar="مورد مرتبط", linked_customer=customer)
        employee = Employee.objects.create(name_ar="موظف مرتبط", linked_customer=customer)

        order = Order.objects.create(
            order_type=Order.OrderType.TAKEAWAY,
            status=Order.Status.CHECKED_OUT,
            customer=customer,
        )
        invoice = SaleInvoice.objects.create(
            invoice_number="CUST-DEL-INV-1",
            order=order,
            customer=customer,
            subtotal=Decimal("15.00"),
            total=Decimal("15.00"),
        )
        CustomerLedgerEntry.objects.create(
            customer=customer,
            entry_type=CustomerLedgerEntry.EntryType.PAYMENT,
            amount=Decimal("-15.00"),
            reference_model="contacts.CustomerLedgerEntry",
            reference_pk="1",
        )
        audit = AuditLog.objects.create(
            action=TREASURY_VOUCHER_AUDIT_ACTION,
            model_label="treasury.UnifiedVoucher",
            object_pk="",
            payload={
                "voucher_type": "receipt",
                "party_type": "customer",
                "customer_pk": customer.pk,
                "ledger_entry_pk": 12345,
            },
        )

        resp = self.client.post(reverse("shell:customer_delete", args=[customer.pk]))
        self.assertEqual(resp.status_code, 302)

        order.refresh_from_db()
        invoice.refresh_from_db()
        supplier.refresh_from_db()
        employee.refresh_from_db()

        self.assertFalse(Customer.objects.filter(pk=customer.pk).exists())
        self.assertFalse(CustomerLedgerEntry.objects.filter(customer_id=customer.pk).exists())
        self.assertIsNone(order.customer_id)
        self.assertIsNone(invoice.customer_id)
        self.assertIsNone(supplier.linked_customer_id)
        self.assertIsNone(employee.linked_customer_id)
        self.assertFalse(AuditLog.objects.filter(pk=audit.pk).exists())
