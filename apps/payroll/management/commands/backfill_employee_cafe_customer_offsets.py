"""إصلاح ذمم عملاء مرتبطين بموظفين: قيود الترحيل إلى مشتريات المقهى الناقصة قبل التحديث."""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.core.decimalutil import as_decimal
from apps.payroll.models import EmployeeCafePurchase


class Command(BaseCommand):
    help = (
        "للمشتريات المسجّلة كـ EmployeeCafePurchase من فاتورة آجل: إنشاء قيد تسوية سالب على العميل "
        "إن كان مفقوداً، ثم إعادة حساب رصيد العميل من دفتره."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="عرض ما سيُصلح دون كتابة في قاعدة البيانات",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        fixed = 0
        skipped = 0

        qs = EmployeeCafePurchase.objects.filter(sale_invoice__isnull=False).select_related(
            "employee", "sale_invoice", "sale_invoice__customer"
        )
        for cp in qs.iterator():
            inv = cp.sale_invoice
            emp = cp.employee
            if not inv or not inv.customer_id:
                skipped += 1
                continue
            if emp.linked_customer_id != inv.customer_id:
                skipped += 1
                continue
            exists = CustomerLedgerEntry.objects.filter(
                customer_id=inv.customer_id,
                reference_model="payroll.EmployeeCafePurchase",
                reference_pk=str(cp.pk),
            ).exists()
            if exists:
                skipped += 1
                continue

            amt = as_decimal(cp.amount).quantize(Decimal("0.01"))
            if amt <= 0:
                skipped += 1
                continue

            self.stdout.write(
                f"سيتم إضافة تسوية -{amt} للعميل {inv.customer_id} (مشتريات مقهى pk={cp.pk} فاتورة {inv.invoice_number})"
            )
            if dry:
                fixed += 1
                continue

            with transaction.atomic():
                CustomerLedgerEntry.objects.create(
                    customer_id=inv.customer_id,
                    entry_type=CustomerLedgerEntry.EntryType.ADJUSTMENT,
                    amount=-amt,
                    note=f"ترحيل ذمة آجل إلى مشتريات مقهى الموظف — {inv.invoice_number} (إصلاح بيانات)",
                    reference_model="payroll.EmployeeCafePurchase",
                    reference_pk=str(cp.pk),
                )
                cust = Customer.objects.select_for_update().get(pk=inv.customer_id)
                cust.balance = cust.computed_balance
                cust.save(update_fields=["balance", "updated_at"])
            fixed += 1

        self.stdout.write(self.style.SUCCESS(f"تم: {fixed}، تخطي: {skipped}" + (" (dry-run)" if dry else "")))
