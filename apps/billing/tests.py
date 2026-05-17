from collections import defaultdict
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.billing.sale_invoice_edit import _payments_from_sale_edit_post


class SaleEditMixedPaymentParseTests(SimpleTestCase):
    @patch(
        "apps.billing.sale_invoice_edit.get_payment_method_codes",
        return_value=["cash", "credit", "bank_palestine"],
    )
    def test_parses_tuple_splits_json(self, _mock):
        post = {
            "use_payment_splits": "1",
            "payment_splits_json": '[["cash", 14], ["credit", 10]]',
            "payer_name": "",
            "payer_phone": "",
        }
        rows = _payments_from_sale_edit_post(post)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "cash")
        self.assertEqual(str(rows[0][1]), "14")
        self.assertEqual(rows[1][0], "credit")

    @patch(
        "apps.billing.sale_invoice_edit.get_payment_method_codes",
        return_value=["cash"],
    )
    def test_use_splits_without_json_raises(self, _mock):
        with self.assertRaises(ValueError) as ctx:
            _payments_from_sale_edit_post(
                {"use_payment_splits": "1", "payment_splits_json": ""}
            )
        self.assertEqual(str(ctx.exception), "INVALID_PAYMENT_SPLITS")


class SaleEditPaymentRowsJournalPrepTests(SimpleTestCase):
    def test_mixed_payment_four_tuple_rows_aggregate_by_method(self):
        """تراجع: تجميع الدفعات للقيود يفكّك 4 قيم (طريقة، مبلغ، محوّل، جوال)."""
        payment_rows = [
            ("credit", Decimal("6"), "ابو كس", "0599123456"),
            ("cash", Decimal("24"), "", ""),
        ]
        pay_by_method = defaultdict(lambda: Decimal("0"))
        for method, amount, _pn, _ph in payment_rows:
            amt = Decimal(amount)
            if amt <= 0:
                continue
            pay_by_method[str(method).strip().lower()] += amt
        self.assertEqual(pay_by_method["credit"], Decimal("6"))
        self.assertEqual(pay_by_method["cash"], Decimal("24"))
