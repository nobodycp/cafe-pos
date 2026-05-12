from decimal import Decimal

from django.test import RequestFactory, TestCase

from apps.catalog.models import Product, Unit
from apps.purchasing.views import _purchase_lines_from_request


class PurchaseLinesFromRequestTests(TestCase):
    """Regression: فاتورة الشراء — تحليل أسطر POST (منتج + كمية + تكلفة)."""

    def setUp(self):
        self.unit = Unit.objects.create(name_ar="قطعة", code="t-pc-pur", name_en="")
        self.product = Product.objects.create(
            name_ar="صنف اختبار شراء",
            product_type=Product.ProductType.RAW,
            unit=self.unit,
            selling_price=Decimal("0"),
            is_stock_tracked=True,
            is_active=True,
        )

    def test_valid_line_parsed(self):
        rf = RequestFactory()
        req = rf.post(
            "/fake/",
            {
                "product_0": str(self.product.pk),
                "qty_0": "10",
                "cost_0": "5",
                "discount_0": "0",
            },
        )
        errors: list = []
        lines = _purchase_lines_from_request(req, errors)
        self.assertEqual(errors, [])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][0].pk, self.product.pk)
        self.assertEqual(lines[0][1], Decimal("10"))

    def test_product_selected_without_qty_row_error_no_duplicate_generic(self):
        rf = RequestFactory()
        req = rf.post(
            "/fake/",
            {
                "product_0": str(self.product.pk),
                "qty_0": "",
                "cost_0": "5",
            },
        )
        errors: list = []
        lines = _purchase_lines_from_request(req, errors)
        self.assertEqual(lines, [])
        self.assertTrue(any("الكمية" in e for e in errors))
        self.assertFalse(any("يرجى إدخال صنف واحد على الأقل" in e for e in errors))

    def test_product_selected_without_cost_row_error(self):
        rf = RequestFactory()
        req = rf.post(
            "/fake/",
            {
                "product_0": str(self.product.pk),
                "qty_0": "3",
                "cost_0": "",
            },
        )
        errors: list = []
        lines = _purchase_lines_from_request(req, errors)
        self.assertEqual(lines, [])
        self.assertTrue(any("تكلفة الوحدة" in e for e in errors))

    def test_empty_lines_generic_message(self):
        rf = RequestFactory()
        req = rf.post("/fake/", {})
        errors: list = []
        lines = _purchase_lines_from_request(req, errors)
        self.assertEqual(lines, [])
        self.assertIn("يرجى إدخال صنف واحد على الأقل", errors)
