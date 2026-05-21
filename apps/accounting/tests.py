from datetime import date
from decimal import Decimal
import uuid

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounting.chart_defaults import DEFAULT_SYSTEM_ACCOUNTS, ensure_default_chart_accounts
from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.accounting.services import (
    MANUAL_ENTRY_REFERENCE,
    create_manual_transfer_entry,
    post_purchase_invoice_journal,
)
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


class ManualJournalEntryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="manual_je", password="x")
        ensure_default_chart_accounts()
        self.from_acc = Account.objects.get(system_code="AR")
        self.to_acc = Account.objects.get(system_code="EXP_OTHER")

    def test_manual_transfer_creates_balanced_two_line_entry(self):
        entry = create_manual_transfer_entry(
            date=date.today(),
            description="ضيافة — نقل إلى مصروف",
            from_account=self.from_acc,
            to_account=self.to_acc,
            amount=Decimal("150.00"),
            user=self.user,
        )
        self.assertTrue(entry.is_balanced)
        self.assertEqual(entry.reference_type, MANUAL_ENTRY_REFERENCE)
        self.assertIsNone(entry.work_session_id)
        self.assertEqual(entry.user_id, self.user.pk)

        lines = list(entry.lines.select_related("account").order_by("debit", "pk"))
        self.assertEqual(len(lines), 2)
        debit_line = next(ln for ln in lines if ln.debit > 0)
        credit_line = next(ln for ln in lines if ln.credit > 0)
        self.assertEqual(debit_line.account_id, self.to_acc.pk)
        self.assertEqual(debit_line.debit, Decimal("150.00"))
        self.assertEqual(credit_line.account_id, self.from_acc.pk)
        self.assertEqual(credit_line.credit, Decimal("150.00"))

    def test_journal_transfer_view_post(self):
        client = Client()
        client.force_login(self.user)
        url = reverse("shell:journal_transfer")
        response = client.post(
            url,
            {
                "date": date.today().isoformat(),
                "description": "نقل ضيافة",
                "from_account": self.from_acc.pk,
                "to_account": self.to_acc.pk,
                "amount": "75.50",
            },
        )
        self.assertEqual(response.status_code, 302)
        entry = JournalEntry.objects.filter(reference_type=MANUAL_ENTRY_REFERENCE).latest("pk")
        self.assertTrue(entry.is_balanced)
        self.assertEqual(entry.lines.count(), 2)


class AccountLedgerViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ledger_t", password="x")
        self.client = Client()
        self.client.force_login(self.user)
        ensure_default_chart_accounts()
        self.account = Account.objects.filter(is_active=True).first()
        entry = JournalEntry.objects.create(
            entry_number="JE-LEDGER-1",
            date=date.today(),
            description="اختبار كشف",
        )
        JournalLine.objects.create(
            entry=entry,
            account=self.account,
            debit=Decimal("10.00"),
            credit=Decimal("0"),
        )

    def test_account_ledger_page_renders_with_pagination(self):
        url = reverse("shell:account_ledger", args=[self.account.pk])
        response = self.client.get(url, {"per_page": 25})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "JE-LEDGER-1")
