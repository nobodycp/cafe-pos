from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounting.chart_defaults import ensure_default_chart_accounts
from apps.accounting.models import JournalEntry
from apps.billing.models import SaleInvoice, SaleInvoiceLine
from apps.billing.tab_service import compute_order_totals
from apps.catalog.models import Category, Product, Unit
from apps.core.models import PaymentMethod, WorkSession
from apps.pos.models import Order, OrderLine

from apps.billing.sale_invoice_edit import (
    apply_sale_invoice_full_edit,
    _payments_from_sale_edit_post,
    format_discount_input_value,
    parse_discount_from_post,
    parse_invoice_date_from_post,
    parse_order_date_from_post,
)


class SaleEditMixedPaymentParseTests(SimpleTestCase):
    @patch(
        "apps.billing.sale_invoice_edit.get_payment_method_codes",
        return_value=["cash", "credit", "bank_palestine"],
    )
    def test_parses_tuple_splits_json(self, _mock):
        post = {
            "use_payment_splits": "1",
            "payment_splits_json": '[["cash", 14], ["credit", 10]]',
            "payer_name": "",
            "payer_phone": "",
        }
        rows = _payments_from_sale_edit_post(post)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "cash")
        self.assertEqual(str(rows[0][1]), "14")
        self.assertEqual(rows[1][0], "credit")

    @patch(
        "apps.billing.sale_invoice_edit.get_payment_method_codes",
        return_value=["cash"],
    )
    def test_use_splits_without_json_raises(self, _mock):
        with self.assertRaises(ValueError) as ctx:
            _payments_from_sale_edit_post(
                {"use_payment_splits": "1", "payment_splits_json": ""}
            )
        self.assertEqual(str(ctx.exception), "INVALID_PAYMENT_SPLITS")


class SaleInvoiceEditDateParseTests(SimpleTestCase):
    def test_returns_none_when_no_value(self):
        self.assertIsNone(parse_invoice_date_from_post({}))
        self.assertIsNone(parse_invoice_date_from_post({"invoice_date": "   "}))

    def test_parses_datetime_local_format(self):
        dt = parse_invoice_date_from_post({"invoice_date": "2025-03-15T09:30"})
        self.assertIsNotNone(dt)
        self.assertTrue(timezone.is_aware(dt))
        local = timezone.localtime(dt)
        self.assertEqual((local.year, local.month, local.day, local.hour, local.minute), (2025, 3, 15, 9, 30))

    def test_parses_date_only_preserves_fallback_time(self):
        fallback = timezone.make_aware(datetime(2025, 1, 2, 14, 25, 7))
        dt = parse_invoice_date_from_post({"invoice_date": "2025-04-10"}, fallback=fallback)
        local = timezone.localtime(dt)
        self.assertEqual(local.date().isoformat(), "2025-04-10")
        self.assertEqual((local.hour, local.minute, local.second), (14, 25, 7))

    def test_rejects_invalid_format(self):
        with self.assertRaises(ValueError) as ctx:
            parse_invoice_date_from_post({"invoice_date": "15-03-2025"})
        self.assertEqual(str(ctx.exception), "INVALID_INVOICE_DATE")


class PosCheckoutOrderDateParseTests(SimpleTestCase):
    def test_order_date_field(self):
        dt = parse_order_date_from_post({"order_date": "2024-06-01"})
        self.assertIsNotNone(dt)
        self.assertEqual(timezone.localtime(dt).date().isoformat(), "2024-06-01")

    def test_transaction_date_alias(self):
        dt = parse_order_date_from_post({"transaction_date": "2024-06-02"})
        self.assertEqual(timezone.localtime(dt).date().isoformat(), "2024-06-02")


class SaleEditDiscountParseTests(SimpleTestCase):
    def test_empty_returns_zero(self):
        a, p, t = parse_discount_from_post({}, subtotal=Decimal("100"))
        self.assertEqual((a, p, t), (Decimal("0"), Decimal("0"), Decimal("0")))
        a, p, t = parse_discount_from_post({"discount": "   "}, subtotal=Decimal("100"))
        self.assertEqual((a, p, t), (Decimal("0"), Decimal("0"), Decimal("0")))

    def test_fixed_amount(self):
        a, p, t = parse_discount_from_post({"discount": "3.50"}, subtotal=Decimal("100"))
        self.assertEqual(a, Decimal("3.50"))
        self.assertEqual(p, Decimal("0"))
        self.assertEqual(t, Decimal("3.50"))

    def test_percent_with_latin_sign(self):
        a, p, t = parse_discount_from_post({"discount": "10%"}, subtotal=Decimal("80"))
        self.assertEqual(a, Decimal("0"))
        self.assertEqual(p, Decimal("10"))
        self.assertEqual(t, Decimal("8.00"))

    def test_percent_with_arabic_sign(self):
        a, p, t = parse_discount_from_post({"discount": "5٪"}, subtotal=Decimal("200"))
        self.assertEqual(p, Decimal("5"))
        self.assertEqual(t, Decimal("10.00"))

    def test_invalid_returns_zero(self):
        a, p, t = parse_discount_from_post({"discount": "abc"}, subtotal=Decimal("100"))
        self.assertEqual((a, p, t), (Decimal("0"), Decimal("0"), Decimal("0")))

    def test_percent_clamped_to_subtotal(self):
        a, p, t = parse_discount_from_post({"discount": "200%"}, subtotal=Decimal("50"))
        self.assertEqual(p, Decimal("100"))
        self.assertEqual(t, Decimal("50.00"))

    def test_fixed_clamped_to_subtotal(self):
        _, _, t = parse_discount_from_post({"discount": "999"}, subtotal=Decimal("40"))
        self.assertEqual(t, Decimal("40.00"))

    def test_negative_treated_as_zero(self):
        a, p, t = parse_discount_from_post({"discount": "-5"}, subtotal=Decimal("100"))
        self.assertEqual((a, p, t), (Decimal("0"), Decimal("0"), Decimal("0")))


class SaleEditDiscountFormatTests(SimpleTestCase):
    def test_format_percent_drops_trailing_zeros(self):
        self.assertEqual(
            format_discount_input_value(discount_amount=Decimal("0"), discount_percent=Decimal("10.00")),
            "10%",
        )

    def test_format_amount_two_decimals(self):
        self.assertEqual(
            format_discount_input_value(discount_amount=Decimal("3"), discount_percent=Decimal("0")),
            "3.00",
        )

    def test_format_empty_when_zero(self):
        self.assertEqual(
            format_discount_input_value(discount_amount=Decimal("0"), discount_percent=Decimal("0")),
            "",
        )


class SaleEditPaymentRowsJournalPrepTests(SimpleTestCase):
    def test_mixed_payment_four_tuple_rows_aggregate_by_method(self):
        """تراجع: تجميع الدفعات للقيود يفكّك 4 قيم (طريقة، مبلغ، محوّل، جوال)."""
        payment_rows = [
            ("credit", Decimal("6"), "ابو كس", "0599123456"),
            ("cash", Decimal("24"), "", ""),
        ]
        pay_by_method = defaultdict(lambda: Decimal("0"))
        for method, amount, _pn, _ph in payment_rows:
            amt = Decimal(amount)
            if amt <= 0:
                continue
            pay_by_method[str(method).strip().lower()] += amt
        self.assertEqual(pay_by_method["credit"], Decimal("6"))
        self.assertEqual(pay_by_method["cash"], Decimal("24"))


User = get_user_model()


class PosCheckoutOrderDateIntegrationTests(TestCase):
    """دفع السلة بتاريخ أمس — ``created_at`` للفاتورة و``entry_date`` للقيد."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="pos_co_date", password="pass-12345")
        PaymentMethod.objects.get_or_create(
            code="cash",
            defaults={"label_ar": "كاش", "ledger": "cash", "sort_order": 0, "is_active": True},
        )
        ensure_default_chart_accounts()
        cls.category = Category.objects.create(name_ar="تصنيف")
        cls.unit = Unit.objects.create(code="u_co", name_ar="وحدة")
        cls.product = Product.objects.create(
            name_ar="قهوة",
            product_type=Product.ProductType.SERVICE,
            category=cls.category,
            unit=cls.unit,
            selling_price=Decimal("25.00"),
        )

    def setUp(self):
        from apps.core.models import PosSettings

        PosSettings.objects.update_or_create(pk=1, defaults={"operation_mode": "shifts"})
        WorkSession.objects.filter(status=WorkSession.Status.OPEN).update(status=WorkSession.Status.CLOSED)
        self.ws = WorkSession.objects.create(
            opened_by=self.user,
            opening_cash=Decimal("0"),
            opening_balances_json={"cash": "0"},
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.yesterday = date.today() - timedelta(days=1)
        self.yesterday_str = self.yesterday.isoformat()

    def _checkout_order(self, order_type: str):
        order = Order.objects.create(
            work_session=self.ws,
            order_type=order_type,
            status=Order.Status.OPEN,
        )
        OrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=Decimal("1"),
            unit_price=Decimal("25.00"),
        )
        grand = compute_order_totals(order)["grand"]
        url = reverse("pos:order_checkout", kwargs={"order_id": order.pk})
        return self.client.post(
            url,
            {
                "payment_mode": "cash",
                "pay_amount": str(grand),
                "order_date": self.yesterday_str,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_checkout_yesterday_sets_invoice_and_journal_date(self):
        resp = self._checkout_order(Order.OrderType.TAKEAWAY)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("ok"))

        order = Order.objects.get(order_type=Order.OrderType.TAKEAWAY, status=Order.Status.CHECKED_OUT)
        inv = SaleInvoice.objects.get(order=order)
        self.assertEqual(timezone.localtime(order.created_at).date(), self.yesterday)
        self.assertEqual(timezone.localtime(inv.created_at).date(), self.yesterday)

        entry = JournalEntry.objects.filter(
            reference_type="billing.SaleInvoice",
            reference_pk=str(inv.pk),
        ).exclude(description__startswith="عكس قيد").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.date, self.yesterday)

    def test_checkout_date_applies_to_dine_in_and_delivery(self):
        for otype in (Order.OrderType.DINE_IN, Order.OrderType.DELIVERY):
            with self.subTest(order_type=otype):
                resp = self._checkout_order(otype)
                self.assertEqual(resp.status_code, 200, resp.content)
                inv = SaleInvoice.objects.filter(order__order_type=otype).latest("pk")
                self.assertEqual(timezone.localtime(inv.created_at).date(), self.yesterday)


class SaleInvoiceEditContinuousModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="sale_edit_cont", password="pass-12345")
        PaymentMethod.objects.get_or_create(
            code="cash",
            defaults={"label_ar": "كاش", "ledger": "cash", "sort_order": 0, "is_active": True},
        )
        cls.category = Category.objects.create(name_ar="تصنيف تعديل")
        cls.unit = Unit.objects.create(code="u_edit", name_ar="وحدة")
        cls.product = Product.objects.create(
            name_ar="مشروب",
            product_type=Product.ProductType.SERVICE,
            category=cls.category,
            unit=cls.unit,
            selling_price=Decimal("10.00"),
        )

    def setUp(self):
        from apps.core.models import PosSettings

        PosSettings.objects.update_or_create(
            pk=1,
            defaults={"operation_mode": "continuous", "allow_sale_invoice_edit": True},
        )

    @patch("apps.accounting.services.post_sale_invoice_journal")
    def test_full_edit_allows_invoice_without_session_in_continuous(self, _mock_post_journal):
        order = Order.objects.create(
            work_session=None,
            order_type=Order.OrderType.TAKEAWAY,
            status=Order.Status.CHECKED_OUT,
        )
        inv = SaleInvoice.objects.create(
            invoice_number="INV-CONT-EDIT-1",
            order=order,
            work_session=None,
            subtotal=Decimal("10.00"),
            discount_total=Decimal("0.00"),
            total=Decimal("10.00"),
        )
        SaleInvoiceLine.objects.create(
            invoice=inv,
            product=self.product,
            quantity=Decimal("1"),
            unit_price=Decimal("10.00"),
            line_subtotal=Decimal("10.00"),
        )
        from apps.billing.models import InvoicePayment

        InvoicePayment.objects.create(invoice=inv, method="cash", amount=Decimal("10.00"))

        apply_sale_invoice_full_edit(
            invoice=inv,
            user=self.user,
            line_rows=[(self.product, Decimal("1"), Decimal("12.00"))],
            payment_rows=[("cash", Decimal("12.00"), "", "")],
            post={"discount": ""},
        )

        inv.refresh_from_db()
        self.assertEqual(inv.total, Decimal("12.00"))
