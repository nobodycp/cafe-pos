from django.test import TestCase

from apps.catalog.models import Product
from apps.inventory.models import StockBalance
from apps.inventory.services import stock_home_base_queryset, sync_missing_stock_balance_rows


class StockHomeAllProductTypesTests(TestCase):
    def test_stock_home_includes_manufactured_service_commission_when_tracked(self):
        m = Product.objects.create(
            name_ar="صنع مخزون",
            product_type=Product.ProductType.MANUFACTURED,
            is_stock_tracked=True,
        )
        s = Product.objects.create(
            name_ar="خدمة مخزون",
            product_type=Product.ProductType.SERVICE,
            is_stock_tracked=True,
        )
        c = Product.objects.create(
            name_ar="عمولة مخزون",
            product_type=Product.ProductType.COMMISSION,
            is_stock_tracked=True,
        )
        sync_missing_stock_balance_rows()
        ids = set(stock_home_base_queryset().values_list("product_id", flat=True))
        self.assertIn(m.pk, ids)
        self.assertIn(s.pk, ids)
        self.assertIn(c.pk, ids)

    def test_sync_creates_balance_row(self):
        p = Product.objects.create(
            name_ar="بدون رصيد",
            product_type=Product.ProductType.READY,
            is_stock_tracked=True,
        )
        self.assertFalse(StockBalance.objects.filter(product=p).exists())
        n = sync_missing_stock_balance_rows()
        self.assertGreaterEqual(n, 1)
        self.assertTrue(StockBalance.objects.filter(product=p).exists())
