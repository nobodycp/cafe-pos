from decimal import Decimal
import uuid

from django.contrib.auth.models import User
from django.test import TestCase

from apps.accounting.chart_defaults import DEFAULT_SYSTEM_ACCOUNTS, ensure_default_chart_accounts
from apps.accounting.models import Account, JournalEntry
from apps.accounting.services import post_purchase_invoice_journal
from apps.purchasing.models import PurchaseInvoice, Supplier


class ChartDefaultsTests(TestCase):
    def test_ensure_recreates_inventory_after_delete(self):
        Account.objects.filter(system_code="INVENTORY").delete()
        self.assertFalse(Account.objects.filter(system_code="INVENTORY").exists())
        ensure_default_chart_accounts()
        inv = Account.objects.get(system_code="INVENTORY")
        self.assertTrue(inv.is_active)
        self.assertEqual(inv.code, "1004")

    def test_all_default_system_codes_present_after_ensure(self):
        ensure_default_chart_accounts()
        codes = {row[4] for row in DEFAULT_SYSTEM_ACCOUNTS}
        for sys_code in codes:
            with self.subTest(sys_code=sys_code):
                self.assertTrue(
                    Account.objects.filter(system_code=sys_code, is_active=True).exists(),
                    msg=f"missing {sys_code}",
                )


class PurchaseJournalSelfHealTests(TestCase):
    def test_post_purchase_journal_after_inventory_account_missing(self):
        user = User.objects.create_user(username="acct_t1", password="x")
        sup = Supplier.objects.create(name_ar="مورد اختبار", name_en="", phone="", email="")
        inv = PurchaseInvoice.objects.create(
            invoice_number=f"PUR-TJE-{uuid.uuid4().hex[:12]}",
            supplier=sup,
            total=Decimal("25.00"),
            payment_status=PurchaseInvoice.PaymentStatus.PAID,
        )
        Account.objects.filter(system_code="INVENTORY").delete()
        self.assertFalse(Account.objects.filter(system_code="INVENTORY").exists())

        post_purchase_invoice_journal(
            purchase_invoice=inv,
            pay_by_method={"cash": Decimal("25.00")},
            user=user,
        )

        self.assertTrue(Account.objects.filter(system_code="INVENTORY", is_active=True).exists())
        self.assertTrue(
            JournalEntry.objects.filter(
                reference_type="purchasing.PurchaseInvoice",
                reference_pk=str(inv.pk),
            ).exists()
        )
