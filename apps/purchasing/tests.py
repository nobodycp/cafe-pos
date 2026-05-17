from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.catalog.models import Product, Unit
from apps.contacts.models import Customer
from apps.purchasing.models import Supplier
from apps.purchasing.supplier_list_filters import (
    apply_supplier_filters,
    parse_supplier_filters,
    supplier_list_base_queryset,
)
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


class PurchaseProductsSearchTests(TestCase):
    """بحث أصناف فاتورة الشراء يطابق الاسم الإنجليزي والباركود (مثل الكاشير)."""

    def setUp(self):
        self.unit = Unit.objects.create(name_ar="قطعة", code="t-pc-search", name_en="")
        self.user = get_user_model().objects.create_user("pur_search_u", password="x" * 12)
        self.product = Product.objects.create(
            name_ar="مادة ألف",
            name_en="AlphaMaterial",
            barcode="BC-PUR-999",
            product_type=Product.ProductType.RAW,
            unit=self.unit,
            selling_price=Decimal("0"),
            is_stock_tracked=True,
            is_active=True,
        )

    def test_search_matches_name_ar_name_en_barcode(self):
        self.client.force_login(self.user)
        url = reverse("shell:purchase_products_search")
        for q, label in [("مادة", "ar"), ("Alpha", "en"), ("BC-PUR", "barcode")]:
            with self.subTest(q=q):
                r = self.client.get(url, {"q": q})
                self.assertEqual(r.status_code, 200)
                data = r.json()
                ids = [row["id"] for row in data["results"]]
                self.assertIn(self.product.pk, ids, msg=label)

    def test_search_excludes_manufactured(self):
        m = Product.objects.create(
            name_ar="منتج مصنع للاختبار",
            name_en="ManufacturedX",
            product_type=Product.ProductType.MANUFACTURED,
            unit=self.unit,
            selling_price=Decimal("0"),
            is_active=True,
        )
        self.client.force_login(self.user)
        r = self.client.get(reverse("shell:purchase_products_search"), {"q": "ManufacturedX"})
        self.assertEqual(r.status_code, 200)
        ids = [row["id"] for row in r.json()["results"]]
        self.assertNotIn(m.pk, ids)


class SupplierListFiltersTests(TestCase):
    def test_search_and_hide_zero_net_default(self):
        Supplier.objects.create(name_ar="مورد صفر", balance=Decimal("0"))
        Supplier.objects.create(name_ar="مورد علينا", balance=Decimal("100"))
        qs = apply_supplier_filters(
            supplier_list_base_queryset(),
            parse_supplier_filters({}),
        )
        names = list(qs.values_list("name_ar", flat=True))
        self.assertNotIn("مورد صفر", names)
        self.assertIn("مورد علينا", names)

    def test_net_side_zero_shows_zero_net(self):
        cust = Customer.objects.create(name_ar="عميل مرتبط", balance=Decimal("50"))
        Supplier.objects.create(name_ar="مورد متوازن", balance=Decimal("50"), linked_customer=cust)
        f = parse_supplier_filters({"net_side": "zero", "hide_zero_net": "0"})
        names = list(apply_supplier_filters(supplier_list_base_queryset(), f).values_list("name_ar", flat=True))
        self.assertEqual(names, ["مورد متوازن"])

    def test_search_by_phone(self):
        Supplier.objects.create(name_ar="أ", phone="0599111222", balance=Decimal("10"))
        Supplier.objects.create(name_ar="ب", phone="0500000000", balance=Decimal("10"))
        f = parse_supplier_filters({"q": "99111", "hide_zero_net": "0"})
        names = list(apply_supplier_filters(supplier_list_base_queryset(), f).values_list("name_ar", flat=True))
        self.assertEqual(names, ["أ"])
