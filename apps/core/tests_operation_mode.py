"""اختبارات نمط العمل: ورديات مقابل محاسبة مستمرة."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounting.chart_defaults import ensure_default_chart_accounts
from apps.accounting.models import JournalEntry, JournalLine
from apps.accounting.services import post_sale_invoice_journal
from apps.billing.models import InvoicePayment, SaleInvoice
from apps.billing.services import checkout_order
from apps.catalog.models import Category, Product, Unit
from apps.core.balance_adjustment_service import post_balance_adjustment
from apps.core.gl_accounts import ensure_gl_account_for_payment_method, get_account_for_payment_method
from apps.core.models import PaymentMethod, PosSettings
from apps.core.operation_mode import MODE_CONTINUOUS, MODE_SHIFTS
from apps.core.payment_channel_balance import get_opening_balance
from apps.pos.models import Order, OrderLine
from apps.pos.services import create_order
from apps.reports.payment_boxes import build_payment_boxes_report, pos_cashier_balance_snapshot


class OperationModePosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="opmode", password="pass-12345")
        unit = Unit.objects.create(code="pc_op", name_ar="قطعة")
        cat = Category.objects.create(name_ar="اختبار")
        cls.product = Product.objects.create(
            name_ar="قهوة",
            product_type=Product.ProductType.SERVICE,
            category=cat,
            unit=unit,
            selling_price=Decimal("10.00"),
        )
        ensure_default_chart_accounts()
        for code in ("cash", "bank_ps"):
            pm = PaymentMethod.objects.filter(code=code).first()
            if pm:
                ensure_gl_account_for_payment_method(pm)

    def setUp(self):
        self.client = Client()
        self.client.login(username="opmode", password="pass-12345")

    def _set_mode(self, mode: str):
        PosSettings.objects.update_or_create(pk=1, defaults={"operation_mode": mode})

    def test_continuous_pos_main_without_shift(self):
        self._set_mode(MODE_CONTINUOUS)
        resp = self.client.get(reverse("pos:main"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context.get("pos_ready"))
        self.assertIsNone(resp.context.get("work_session"))

    def test_continuous_pos_main_includes_desk_balance_rows(self):
        self._set_mode(MODE_CONTINUOUS)
        order = create_order(user=self.user, order_type=Order.OrderType.TAKEAWAY)
        OrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=Decimal("1"),
            unit_price=self.product.selling_price,
        )
        checkout_order(
            order=order,
            user=self.user,
            payments=[("cash", Decimal("10.00"))],
        )
        snap = pos_cashier_balance_snapshot(work_session=None)
        self.assertTrue(snap["rows"])
        cash_row = next((r for r in snap["rows"] if r["code"] == "cash"), None)
        self.assertIsNotNone(cash_row)
        self.assertGreaterEqual(cash_row["balance"], Decimal("10.00"))
        resp = self.client.get(reverse("pos:main"))
        self.assertEqual(resp.status_code, 200)
        rows = resp.context.get("desk_balance_rows") or []
        self.assertGreaterEqual(len(rows), 1)
        self.assertIn("balance", rows[0])
        self.assertEqual(resp.context.get("desk_balance_period"), "الرصيد الحالي")
        self.assertIn("المتبقي النهائي".encode(), resp.content)

    def test_shifts_pos_main_blocks_without_shift(self):
        self._set_mode(MODE_SHIFTS)
        resp = self.client.get(reverse("pos:main"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context.get("uses_shifts"))
        self.assertFalse(resp.context.get("pos_ready"))
        self.assertIsNone(resp.context.get("work_session"))
        self.assertIn(b"pos-start-shell", resp.content)

    def test_continuous_checkout_invoice_has_null_work_session(self):
        self._set_mode(MODE_CONTINUOUS)
        order = create_order(user=self.user, order_type=Order.OrderType.TAKEAWAY)
        OrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=Decimal("1"),
            unit_price=self.product.selling_price,
        )
        inv = checkout_order(
            order=order,
            user=self.user,
            payments=[("cash", Decimal("10.00"))],
        )
        inv.refresh_from_db()
        order.refresh_from_db()
        self.assertIsNone(inv.work_session_id)
        self.assertIsNone(order.work_session_id)

    def test_session_summary_redirects_in_continuous(self):
        self._set_mode(MODE_CONTINUOUS)
        resp = self.client.get(reverse("core:session_summary"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("pos:main"))


class PerPaymentMethodGLTests(TestCase):
    def setUp(self):
        ensure_default_chart_accounts()
        self.user = User.objects.create_superuser(username="gl_pm", password="x")
        cash = PaymentMethod.objects.get(code="cash")
        bank = PaymentMethod.objects.filter(code="bank_ps").first() or PaymentMethod.objects.filter(
            ledger="bank"
        ).first()
        ensure_gl_account_for_payment_method(cash)
        ensure_gl_account_for_payment_method(bank)
        self.bank = bank
        self.cash_acc = get_account_for_payment_method("cash")
        self.bank_acc = get_account_for_payment_method(bank.code)

    def test_sale_two_methods_debits_different_gl_accounts(self):
        order = Order.objects.create(
            order_type=Order.OrderType.TAKEAWAY,
            status=Order.Status.CHECKED_OUT,
        )
        inv = SaleInvoice.objects.create(
            invoice_number="GL-SPLIT-1",
            order=order,
            work_session=None,
            total=Decimal("30"),
            subtotal=Decimal("30"),
        )
        InvoicePayment.objects.create(invoice=inv, method="cash", amount=Decimal("10"))
        InvoicePayment.objects.create(invoice=inv, method=self.bank.code, amount=Decimal("20"))
        post_sale_invoice_journal(
            invoice=inv,
            pay_by_method={"cash": Decimal("10"), self.bank.code: Decimal("20")},
            user=self.user,
        )
        entry = JournalEntry.objects.filter(
            reference_type="billing.SaleInvoice",
            reference_pk=str(inv.pk),
        ).first()
        self.assertIsNotNone(entry)
        debits = {
            ln.account_id: ln.debit
            for ln in JournalLine.objects.filter(entry=entry, debit__gt=0)
        }
        self.assertIn(self.cash_acc.pk, debits)
        self.assertIn(self.bank_acc.pk, debits)
        self.assertNotEqual(self.cash_acc.pk, self.bank_acc.pk)


class OpeningBalanceUnifiedTests(TestCase):
    def setUp(self):
        ensure_default_chart_accounts()
        self.user = User.objects.create_superuser(username="open_bal", password="x")
        self.pm = PaymentMethod.objects.get(code="cash")
        ensure_gl_account_for_payment_method(self.pm)

    def test_continuous_adjustment_changes_opening_in_report(self):
        from datetime import timedelta

        from apps.core.payment_channel_balance import get_or_create_channel_balance

        PosSettings.objects.update_or_create(pk=1, defaults={"operation_mode": MODE_CONTINUOUS})
        bal = get_or_create_channel_balance(self.pm)
        bal.opening_balance = Decimal("100")
        bal.save(update_fields=["opening_balance"])
        post_balance_adjustment(
            method=self.pm,
            amount_delta=Decimal("50"),
            reason="رصيد قديم",
            effective_date=date.today() - timedelta(days=1),
            user=self.user,
        )
        opening = get_opening_balance("cash", date_from=date.today(), date_to=date.today())
        self.assertEqual(opening, Decimal("150.00"))
        report = build_payment_boxes_report(
            date_from=date.today(),
            date_to=date.today(),
            payment_method="cash",
        )
        row = report["rows"][0]
        self.assertEqual(row["opening"], Decimal("150.00"))
