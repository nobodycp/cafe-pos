from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Category, Product, Unit
from apps.core.models import WorkSession
from apps.pos.models import Order, OrderLine

User = get_user_model()


class ProductDeleteOpenOrderLineTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u_del", password="pw")
        self.client = Client()
        self.client.login(username="u_del", password="pw")
        self.work_session = WorkSession.objects.create(
            opened_by=self.user,
            status=WorkSession.Status.OPEN,
        )
        self.category = Category.objects.create(name_ar="تصنيف")
        self.unit = Unit.objects.create(code="u_del", name_ar="وحدة")
        self.product = Product.objects.create(
            name_ar="بيض",
            product_type=Product.ProductType.READY,
            category=self.category,
            unit=self.unit,
        )

    def _delete_url(self):
        return reverse("shell:product_delete", args=[self.product.pk])

    def test_delete_blocked_when_open_pos_order_has_line(self):
        order = Order.objects.create(
            work_session=self.work_session,
            order_type=Order.OrderType.TAKEAWAY,
            status=Order.Status.OPEN,
        )
        OrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=Decimal("1"),
            unit_price=Decimal("1"),
        )
        self.client.post(self._delete_url())
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_delete_allowed_when_only_checked_out_order_line(self):
        order = Order.objects.create(
            work_session=self.work_session,
            order_type=Order.OrderType.TAKEAWAY,
            status=Order.Status.CHECKED_OUT,
        )
        OrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=Decimal("1"),
            unit_price=Decimal("1"),
        )
        self.client.post(self._delete_url())
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())

    def test_delete_allowed_when_only_cancelled_order_line(self):
        order = Order.objects.create(
            work_session=self.work_session,
            order_type=Order.OrderType.TAKEAWAY,
            status=Order.Status.CANCELLED,
        )
        OrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=Decimal("1"),
            unit_price=Decimal("1"),
        )
        self.client.post(self._delete_url())
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())


class RawMaterialsSearchTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="rm_search", password="pw")
        self.client = Client()
        self.client.login(username="rm_search", password="pw")
        unit = Unit.objects.create(code="kg", name_ar="كغ")
        Product.objects.create(
            name_ar="سكر ناعم",
            product_type=Product.ProductType.RAW,
            unit=unit,
            is_active=True,
        )
        Product.objects.create(
            name_ar="كابتشينو جاهز",
            product_type=Product.ProductType.READY,
            unit=unit,
            is_active=True,
        )

    def test_search_returns_raw_only(self):
        url = reverse("shell:raw_materials_search")
        r = self.client.get(url, {"q": "سكر"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["name_ar"], "سكر ناعم")
