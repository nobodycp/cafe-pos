"""
ترحيل فواتير البيع بالآجل القديمة (قبل SupplierCafePurchase):

- عميل مرتبط بمورد، فاتورة آجل، بدون مسار موظف، وبدون سجل SupplierCafePurchase بعد.
- إن وُجد قيد مورد قديم من «مشتريات العميل» (مرجع billing.SaleInvoice) يُحدَّث مرجعه إلى SupplierCafePurchase
  ويُضاف قيد تسوية العميل الناقص فقط (بدون تكرار خصم المورد).
- إن لم يُوجد قيد مورد قديم: يُنشأ قيد الخصم كما في النظام الجديد (--allow-new-supplier-ledger).
"""

from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.billing.models import SaleInvoice
from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.core.decimalutil import as_decimal
from apps.core.payment_methods import credit_method_codes
from apps.payroll.models import EmployeeCafePurchase
from apps.purchasing.models import Supplier, SupplierCafePurchase, SupplierLedgerEntry


def _invoice_credit_total(inv: SaleInvoice) -> Decimal:
    total = Decimal("0")
    for p in inv.payments.filter(method__in=credit_method_codes()):
        total += as_decimal(p.amount)
    return total.quantize(Decimal("0.01"))


class Command(BaseCommand):
    help = (
        "إصلاح فواتير آجل قديمة لعملاء مرتبطين بمورد: إنشاء SupplierCafePurchase + تسوية عميل، "
        "وتحديث مرجع قيد المورد القديم إن وُجد."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="عرض ما سيُصلح دون كتابة في قاعدة البيانات",
        )
        parser.add_argument(
            "--allow-new-supplier-ledger",
            action="store_true",
            help="إن لم يُعثر على قيد مورد قديم بمرجع الفاتورة، إنشاء قيد خصم جديد (استخدم بحذر لتجنب ازدواجية).",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        allow_new = options["allow_new_supplier_ledger"]
        fixed = 0
        skipped = 0
        skipped_no_legacy = 0

        qs = (
            SaleInvoice.objects.filter(is_cancelled=False, customer_id__isnull=False)
            .select_related("customer", "work_session")
            .prefetch_related("payments")
            .order_by("pk")
        )

        for inv in qs.iterator():
            cred = _invoice_credit_total(inv)
            if cred <= 0:
                skipped += 1
                continue

            if EmployeeCafePurchase.objects.filter(sale_invoice_id=inv.pk).exists():
                skipped += 1
                continue

            if SupplierCafePurchase.objects.filter(sale_invoice_id=inv.pk).exists():
                skipped += 1
                continue

            sup = (
                Supplier.objects.filter(linked_customer_id=inv.customer_id, is_active=True)
                .only("pk", "balance")
                .first()
            )
            if not sup:
                skipped += 1
                continue

            legacy_rows = list(
                SupplierLedgerEntry.objects.filter(
                    supplier_id=sup.pk,
                    reference_model="billing.SaleInvoice",
                    reference_pk=str(inv.pk),
                    amount__lt=0,
                )
            )
            legacy_match = None
            for le in legacy_rows:
                if abs(as_decimal(le.amount) + cred) <= Decimal("0.02"):
                    legacy_match = le
                    break

            if legacy_match is None and not allow_new:
                skipped_no_legacy += 1
                self.stdout.write(
                    f"تخطي {inv.invoice_number} (مورد {sup.pk}): لا قيد مورد قديم بمرجع الفاتورة — استخدم --allow-new-supplier-ledger إن كان الخصم لم يُسجَّل أبداً."
                )
                continue

            self.stdout.write(
                f"{'[dry] ' if dry else ''}فاتورة {inv.invoice_number} — عميل {inv.customer_id} — مورد {sup.pk} — آجل {cred}"
                + (" — تحديث قيد مورد قديم" if legacy_match else " — إنشاء قيد مورد جديد")
            )

            if dry:
                fixed += 1
                continue

            with transaction.atomic():
                scp = SupplierCafePurchase.objects.create(
                    supplier_id=sup.pk,
                    work_session_id=inv.work_session_id,
                    amount=cred,
                    note=f"فاتورة {inv.invoice_number} (آجل — مقهى) — إصلاح بيانات",
                    sale_invoice_id=inv.pk,
                )
                if legacy_match:
                    leu = SupplierLedgerEntry.objects.select_for_update().get(pk=legacy_match.pk)
                    leu.reference_model = "purchasing.SupplierCafePurchase"
                    leu.reference_pk = str(scp.pk)
                    leu.save(update_fields=["reference_model", "reference_pk", "updated_at"])
                else:
                    sup2 = Supplier.objects.select_for_update().get(pk=sup.pk)
                    sup2.balance = (as_decimal(sup2.balance) - cred).quantize(Decimal("0.01"))
                    if sup2.balance < 0 and sup2.balance > Decimal("-0.01"):
                        sup2.balance = Decimal("0")
                    sup2.save(update_fields=["balance", "updated_at"])
                    SupplierLedgerEntry.objects.create(
                        supplier_id=sup.pk,
                        entry_type=SupplierLedgerEntry.EntryType.ADJUSTMENT,
                        amount=-cred,
                        note=f"مشتريات من المقهى (آجل) — {inv.invoice_number} — إصلاح بيانات",
                        reference_model="purchasing.SupplierCafePurchase",
                        reference_pk=str(scp.pk),
                    )

                CustomerLedgerEntry.objects.create(
                    customer_id=inv.customer_id,
                    entry_type=CustomerLedgerEntry.EntryType.ADJUSTMENT,
                    amount=-cred,
                    note=f"ترحيل ذمة آجل إلى حساب المورد (مشتريات مقهى) — {inv.invoice_number} (إصلاح بيانات)",
                    reference_model="purchasing.SupplierCafePurchase",
                    reference_pk=str(scp.pk),
                )
                cust = Customer.objects.select_for_update().get(pk=inv.customer_id)
                cust.balance = cust.computed_balance
                cust.save(update_fields=["balance", "updated_at"])
                sup3 = Supplier.objects.select_for_update().get(pk=sup.pk)
                sup3.balance = sup3.computed_balance
                sup3.save(update_fields=["balance", "updated_at"])

            fixed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"تم إصلاح: {fixed}، تخطي: {skipped}، تخطي بلا قيد مورد قديم: {skipped_no_legacy}"
                + (" (dry-run)" if dry else "")
            )
        )
