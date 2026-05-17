from datetime import date
from decimal import Decimal

from django.test import RequestFactory, TestCase

from apps.accounting.journal_list_filters import apply_journal_filters, parse_journal_filters
from apps.accounting.models import JournalEntry
from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.core.ledger_pagination import paginate_amount_ledger
from apps.core.list_filters import parse_date_range, parse_iso_date


class ListFiltersUtilTests(TestCase):
    def test_parse_iso_date(self):
        self.assertEqual(parse_iso_date("2026-05-01"), date(2026, 5, 1))
        self.assertIsNone(parse_iso_date("bad"))

    def test_parse_date_range_swaps(self):
        rf = RequestFactory()
        req = rf.get("/", {"date_from": "2026-05-10", "date_to": "2026-05-01"})
        d_from, d_to = parse_date_range(req)
        self.assertEqual(d_from, date(2026, 5, 1))
        self.assertEqual(d_to, date(2026, 5, 10))


class JournalListFilterTests(TestCase):
    def test_filter_by_q_and_status(self):
        JournalEntry.objects.create(
            entry_number="J-100",
            date=date.today(),
            description="اختبار قيد",
            is_reversed=False,
        )
        JournalEntry.objects.create(
            entry_number="J-200",
            date=date.today(),
            description="معكوس",
            is_reversed=True,
        )
        rf = RequestFactory()
        req = rf.get("/", {"q": "J-100", "status": "active"})
        f = parse_journal_filters(req)
        qs = apply_journal_filters(JournalEntry.objects.all(), f)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.get().entry_number, "J-100")


class LedgerPaginationTests(TestCase):
    def test_paginate_amount_ledger_running_balance(self):
        customer = Customer.objects.create(name_ar="عميل ترقيم")
        for i, amt in enumerate([Decimal("10"), Decimal("5"), Decimal("-3")], start=1):
            CustomerLedgerEntry.objects.create(
                customer=customer,
                entry_type=CustomerLedgerEntry.EntryType.ADJUSTMENT,
                amount=amt,
                note=f"قيد {i}",
            )
        entries = customer.ledger_entries.order_by("created_at", "pk")
        rf = RequestFactory()
        req = rf.get("/", {"per_page": "2", "page": "2"})
        pag = paginate_amount_ledger(
            req,
            entries,
            opening_balance=Decimal("0"),
            build_row=lambda e, running: {"running": running},
            per_page_choices=(2, 25, 50),
            default_per_page=2,
        )
        self.assertEqual(len(pag["rows"]), 1)
        self.assertEqual(pag["rows"][0]["running"], Decimal("12.00"))
