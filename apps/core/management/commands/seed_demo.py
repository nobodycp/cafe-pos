from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import Category, Product, RecipeLine, Unit
from apps.contacts.models import Customer
from apps.core.services import SessionService
from apps.expenses.models import ExpenseCategory
from apps.payroll.models import Employee
from apps.pos.models import DiningTable
from apps.purchasing.models import PurchaseInvoice, Supplier
from apps.purchasing.services import post_purchase_invoice


class Command(BaseCommand):
    help = "Seed Arabic demo data (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        admin, _ = User.objects.get_or_create(username="admin", defaults={"is_staff": True, "is_superuser": True})
        admin.is_staff = True
        admin.is_superuser = True
        admin.set_password("admin123")
        admin.save()

        piece, _ = Unit.objects.get_or_create(code="piece", defaults={"name_ar": "قطعة", "name_en": "Piece"})
        kg, _ = Unit.objects.get_or_create(code="kg", defaults={"name_ar": "كيلوغرام", "name_en": "Kilogram"})

        cat_hot, _ = Category.objects.get_or_create(
            name_ar="مشروبات ساخنة",
            defaults={"name_en": "Hot drinks", "sort_order": 1},
        )
        cat_dessert, _ = Category.objects.get_or_create(
            name_ar="حلويات",
            defaults={"name_en": "Desserts", "sort_order": 2},
        )

        coffee, _ = Product.objects.get_or_create(
            name_ar="بن مطحون",
            defaults={
                "category": cat_hot,
                "unit": kg,
                "selling_price": Decimal("80"),
                "product_type": Product.ProductType.RAW,
                "is_stock_tracked": True,
                "min_stock_level": Decimal("1"),
            },
        )
        milk, _ = Product.objects.get_or_create(
            name_ar="حليب",
            defaults={
                "category": cat_hot,
                "unit": kg,
                "selling_price": Decimal("12"),
                "product_type": Product.ProductType.RAW,
                "is_stock_tracked": True,
                "min_stock_level": Decimal("2"),
            },
        )
        cake, _ = Product.objects.get_or_create(
            name_ar="كيكة جاهزة",
            defaults={
                "category": cat_dessert,
                "unit": piece,
                "selling_price": Decimal("15"),
                "product_type": Product.ProductType.READY,
                "is_stock_tracked": True,
                "min_stock_level": Decimal("3"),
            },
        )
        cap, _ = Product.objects.get_or_create(
            name_ar="كابتشينو",
            defaults={
                "category": cat_hot,
                "unit": piece,
                "selling_price": Decimal("18"),
                "product_type": Product.ProductType.MANUFACTURED,
                "is_stock_tracked": False,
            },
        )
        Product.objects.get_or_create(
            name_ar="خدمة توصيل",
            defaults={
                "category": cat_hot,
                "unit": piece,
                "selling_price": Decimal("10"),
                "product_type": Product.ProductType.SERVICE,
                "is_stock_tracked": False,
            },
        )
        Product.objects.get_or_create(
            name_ar="بطاقة شحن (عمولة)",
            defaults={
                "category": cat_hot,
                "unit": piece,
                "selling_price": Decimal("100"),
                "product_type": Product.ProductType.COMMISSION,
                "is_stock_tracked": False,
                "commission_percentage": Decimal("10"),
            },
        )

        RecipeLine.objects.get_or_create(
            manufactured_product=cap,
            component=coffee,
            defaults={"quantity_per_unit": Decimal("0.02")},
        )
        RecipeLine.objects.get_or_create(
            manufactured_product=cap,
            component=milk,
            defaults={"quantity_per_unit": Decimal("0.15")},
        )

        for i in range(1, 7):
            DiningTable.objects.get_or_create(name_ar=f"طاولة {i}", defaults={"sort_order": i, "name_en": f"Table {i}"})

        Supplier.objects.get_or_create(
            name_ar="شركة توريد المواد الغذائية",
            defaults={"name_en": "Food supply co.", "phone": "0500000000", "balance": Decimal("0")},
        )
        Customer.objects.get_or_create(
            name_ar="عميل تجريبي",
            defaults={"name_en": "Demo customer", "phone": "0555555555", "balance": Decimal("0")},
        )
        Employee.objects.get_or_create(
            name_ar="موظف الصندوق",
            defaults={"name_en": "Cashier", "daily_wage": Decimal("100"), "net_balance": Decimal("0")},
        )

        for code, ar, en in [
            (ExpenseCategory.Code.SALARIES, "رواتب", "Salaries"),
            (ExpenseCategory.Code.FUEL, "وقود", "Fuel"),
            (ExpenseCategory.Code.CLEANING, "تنظيف", "Cleaning"),
            (ExpenseCategory.Code.SUPPLIES, "مستلزمات", "Supplies"),
            (ExpenseCategory.Code.INTERNET, "إنترنت واتصالات", "Internet"),
            (ExpenseCategory.Code.TRANSPORT, "نقل", "Transport"),
            (ExpenseCategory.Code.MAINTENANCE, "صيانة", "Maintenance"),
            (ExpenseCategory.Code.OTHER, "أخرى", "Other"),
        ]:
            ExpenseCategory.objects.get_or_create(code=code, defaults={"name_ar": ar, "name_en": en})

        if not SessionService.get_open_session():
            SessionService.open_session(admin, Decimal("0"), "بيانات تجريبية")

        sup = Supplier.objects.order_by("id").first()
        if sup and not PurchaseInvoice.objects.filter(supplier=sup).exists():
            try:
                post_purchase_invoice(
                    supplier=sup,
                    lines=[
                        (coffee, Decimal("5"), Decimal("40")),
                        (milk, Decimal("10"), Decimal("6")),
                        (cake, Decimal("20"), Decimal("8")),
                    ],
                    user=admin,
                    payments=[("cash", Decimal("420"))],
                )
                self.stdout.write(self.style.SUCCESS("Posted initial purchase invoice."))
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Purchase seed skipped: {exc}"))

        self.stdout.write(self.style.SUCCESS("Seed completed. Login: admin / admin123"))
