from decimal import Decimal

from django.test import SimpleTestCase

from apps.contacts.customer_lookup import customer_balance_search_fields, customer_search_result_row


class CustomerBalanceSearchFieldsTests(SimpleTestCase):
    def test_debit_positive(self):
        row = customer_balance_search_fields(Decimal("150.50"))
        self.assertEqual(row["balance"], "150.50")
        self.assertEqual(row["balance_hint"], "عليه")
        self.assertEqual(row["balance_kind"], "debit")

    def test_credit_negative(self):
        row = customer_balance_search_fields(Decimal("-25"))
        self.assertEqual(row["balance"], "-25.00")
        self.assertEqual(row["balance_hint"], "له")
        self.assertEqual(row["balance_kind"], "credit")

    def test_zero(self):
        row = customer_balance_search_fields(Decimal("0"))
        self.assertEqual(row["balance_hint"], "متوازن")
        self.assertEqual(row["balance_kind"], "zero")


class CustomerSearchResultRowTests(SimpleTestCase):
    def test_row_includes_balance_fields(self):
        class _C:
            pk = 1
            name_ar = "أحمد"
            phone = "0599"
            balance = Decimal("10")

        row = customer_search_result_row(_C())
        self.assertEqual(row["id"], 1)
        self.assertEqual(row["name_ar"], "أحمد")
        self.assertEqual(row["balance_hint"], "عليه")
