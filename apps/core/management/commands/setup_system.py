from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.expenses.models import ExpenseCategory


class Command(BaseCommand):
    help = "Setup production system: create admin user + expense categories. No demo data."

    def add_arguments(self, parser):
        parser.add_argument("--admin-pass", type=str, default="", help="Admin password (prompted if empty)")

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()

        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={"is_staff": True, "is_superuser": True},
        )
        if created:
            password = options["admin_pass"]
            if not password:
                import getpass
                while True:
                    password = getpass.getpass("أدخل كلمة مرور المدير: ")
                    confirm = getpass.getpass("أعد كتابة كلمة المرور: ")
                    if password == confirm and len(password) >= 4:
                        break
                    self.stderr.write(self.style.ERROR("كلمات المرور غير متطابقة أو قصيرة جداً"))
            admin.set_password(password)
            admin.is_staff = True
            admin.is_superuser = True
            admin.save()
            self.stdout.write(self.style.SUCCESS(f"تم إنشاء حساب المدير: admin"))
        else:
            self.stdout.write(self.style.WARNING("حساب المدير موجود مسبقاً"))

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

        self.stdout.write(self.style.SUCCESS("تم تجهيز النظام — قاعدة البيانات فارغة وجاهزة للاستخدام"))
