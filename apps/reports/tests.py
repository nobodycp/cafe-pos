from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import InvoicePayment, SaleInvoice
from apps.core.models import PaymentMethod, WorkSession
from apps.expenses.models import Expense, ExpenseCategory
from apps.pos.models import Order
from apps.purchasing.models import Supplier, SupplierPayment
from apps.reports.payment_boxes import build_payment_boxes_report

User = get_user_model()


class PaymentBoxesReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="boxes_report", password="pass-12345")
        PaymentMethod.objects.get_or_create(
            code="cash",
            defaults={"label_ar": "كاش", "ledger": "cash", "sort_order": 0, "is_active": True},
        )
        PaymentMethod.objects.get_or_create(
            code="bank_ps",
            defaults={"label_ar": "بنك فلسطين", "ledger": "bank", "sort_order": 1, "is_active": True},
        )
        cls.category = ExpenseCategory.objects.create(
            code="other_boxes",
            name_ar="أخرى",
            name_en="Other",
        )
        cls.supplier = Supplier.objects.create(name_ar="مورد تجريبي")

    def setUp(self):
        WorkSession.objects.filter(status=WorkSession.Status.OPEN).update(status=WorkSession.Status.CLOSED)
        self.today = date.today()
        self.ws = WorkSession.objects.create(
            opened_by=self.user,
            opening_cash=Decimal("100"),
            opening_balances_json={"cash": "100", "bank_ps": "50"},
        )

    def _make_invoice_payment(self, method: str, amount: Decimal, on_date: Optional[date] = None):
        on_date = on_date or self.today
        order = Order.objects.create(
            work_session=self.ws,
            order_type=Order.OrderType.TAKEAWAY,
            status=Order.Status.CHECKED_OUT,
        )
        inv = SaleInvoice.objects.create(
            work_session=self.ws,
            order=order,
            invoice_number=f"BOX-{method}-{on_date.isoformat()}-{InvoicePayment.objects.count()}",
            total=amount,
            subtotal=amount,
        )
        SaleInvoice.objects.filter(pk=inv.pk).update(
            created_at=timezone.make_aware(datetime.combine(on_date, datetime.min.time()))
        )
        inv.refresh_from_db()
        return InvoicePayment.objects.create(invoice=inv, method=method, amount=amount)

    def test_balance_equals_opening_plus_inflow_minus_outflow(self):
        self._make_invoice_payment("cash", Decimal("300"))
        Expense.objects.create(
            work_session=self.ws,
            category=self.category,
            expense_date=self.today,
            amount=Decimal("80"),
            payment_method="cash",
        )
        report = build_payment_boxes_report(self.today, self.today)
        row = next(r for r in report["rows"] if r["code"] == "cash")
        self.assertEqual(row["opening"], Decimal("100.00"))
        self.assertEqual(row["inflow"], Decimal("300.00"))
        self.assertEqual(row["outflow"], Decimal("80.00"))
        self.assertEqual(row["balance"], Decimal("320.00"))
        self.assertEqual(
            row["balance"],
            (row["opening"] + row["inflow"] - row["outflow"]).quantize(Decimal("0.01")),
        )

    def test_date_filter_excludes_out_of_range(self):
        yesterday = self.today - timedelta(days=1)
        tomorrow = self.today + timedelta(days=1)
        self._make_invoice_payment("bank_ps", Decimal("200"), on_date=yesterday)
        self._make_invoice_payment("bank_ps", Decimal("400"), on_date=self.today)
        self._make_invoice_payment("bank_ps", Decimal("999"), on_date=tomorrow)

        report = build_payment_boxes_report(self.today, self.today)
        row = next(r for r in report["rows"] if r["code"] == "bank_ps")
        self.assertEqual(row["inflow"], Decimal("400.00"))

    def test_supplier_payment_counts_as_outflow(self):
        SupplierPayment.objects.create(
            supplier=self.supplier,
            work_session=self.ws,
            amount=Decimal("150"),
            method="bank_ps",
        )
        report = build_payment_boxes_report(self.today, self.today)
        row = next(r for r in report["rows"] if r["code"] == "bank_ps")
        self.assertEqual(row["outflow"], Decimal("150.00"))
        self.assertEqual(row["opening"], Decimal("50.00"))

    def test_payment_boxes_page_renders(self):
        self.client.login(username="boxes_report", password="pass-12345")
        url = reverse("shell:payment_boxes")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "الصناديق")
        self.assertContains(resp, "session-reconcile-table")
