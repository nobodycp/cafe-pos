from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import Product, RecipeLine
from apps.inventory.models import ManufacturingBatch, StockBalance
from apps.inventory.models import StockMovement
from apps.inventory.services import (
    adjust_stock,
    movement_rows_with_balance_after,
    record_manufacturing_batch,
    stock_home_base_queryset,
    sync_missing_stock_balance_rows,
    void_manufacturing_batch,
)


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


class MovementBalanceAfterTests(TestCase):
    def test_balance_after_includes_opening_and_chronological_deltas(self):
        p = Product.objects.create(
            name_ar="صنف حركات",
            product_type=Product.ProductType.RAW,
            is_stock_tracked=True,
        )
        StockBalance.objects.create(product=p, quantity_on_hand=Decimal("15"), average_cost=Decimal("1"))
        m1 = adjust_stock(
            product=p,
            quantity_delta=Decimal("10"),
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            session=None,
            reference_model="manual",
            reference_pk="1",
        )
        m2 = adjust_stock(
            product=p,
            quantity_delta=Decimal("-3"),
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            session=None,
            reference_model="manual",
            reference_pk="2",
        )
        adjust_stock(
            product=p,
            quantity_delta=Decimal("5"),
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            session=None,
            reference_model="manual",
            reference_pk="3",
        )
        rows = movement_rows_with_balance_after([m2, m1])
        by_pk = {r["movement"].pk: r["balance_after"] for r in rows}
        self.assertEqual(by_pk[m1.pk], Decimal("25"))
        self.assertEqual(by_pk[m2.pk], Decimal("22"))


class VoidManufacturingBatchTests(TestCase):
    def test_void_restores_components_and_finished_stock(self):
        raw = Product.objects.create(
            name_ar="مكوّن خام",
            product_type=Product.ProductType.RAW,
            is_stock_tracked=True,
        )
        mfr = Product.objects.create(
            name_ar="منتج مصنع",
            product_type=Product.ProductType.MANUFACTURED,
            is_stock_tracked=True,
        )
        RecipeLine.objects.create(
            manufactured_product=mfr,
            component=raw,
            quantity_per_unit=Decimal("2"),
        )
        StockBalance.objects.create(product=raw, quantity_on_hand=Decimal("100"), average_cost=Decimal("1"))
        StockBalance.objects.create(product=mfr, quantity_on_hand=Decimal("0"), average_cost=Decimal("0"))
        batch = record_manufacturing_batch(product=mfr, quantity=Decimal("3"), session=None, note="اختبار")
        self.assertEqual(StockBalance.objects.get(product=raw).quantity_on_hand, Decimal("94"))
        self.assertEqual(StockBalance.objects.get(product=mfr).quantity_on_hand, Decimal("3"))
        void_manufacturing_batch(batch=batch)
        self.assertFalse(ManufacturingBatch.objects.filter(pk=batch.pk).exists())
        self.assertEqual(StockBalance.objects.get(product=raw).quantity_on_hand, Decimal("100"))
        self.assertEqual(StockBalance.objects.get(product=mfr).quantity_on_hand, Decimal("0"))
