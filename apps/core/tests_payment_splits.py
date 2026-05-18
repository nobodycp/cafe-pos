from decimal import Decimal

from django.test import SimpleTestCase

from apps.core.payment_splits import PaymentSplitsParseError, parse_payment_splits_json


class PaymentSplitsParseTests(SimpleTestCase):
    def test_tuple_rows(self):
        lines = parse_payment_splits_json(
            '[["cash", "10.50"], ["bank_ps", 5]]',
            allowed_codes=frozenset({"cash", "bank_ps", "credit"}),
            quantize=True,
        )
        self.assertEqual(
            lines,
            [("cash", Decimal("10.50")), ("bank_ps", Decimal("5.00"))],
        )

    def test_dict_rows(self):
        lines = parse_payment_splits_json(
            '[{"method": "cash", "amount": "3"}, {"method": "credit", "amount": 2}]',
            allowed_codes=frozenset({"cash", "credit"}),
        )
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0][0], "cash")

    def test_skips_zero_amounts(self):
        lines = parse_payment_splits_json(
            '[["cash", 0], ["cash", -1], ["cash", "4"]]',
            allowed_codes=frozenset({"cash"}),
        )
        self.assertEqual(lines, [("cash", Decimal("4"))])

    def test_invalid_json(self):
        with self.assertRaises(PaymentSplitsParseError) as ctx:
            parse_payment_splits_json("not-json", allowed_codes=frozenset({"cash"}))
        self.assertEqual(ctx.exception.code, "INVALID_JSON")

    def test_invalid_shape(self):
        with self.assertRaises(PaymentSplitsParseError) as ctx:
            parse_payment_splits_json('{"cash": 1}', allowed_codes=frozenset({"cash"}))
        self.assertEqual(ctx.exception.code, "INVALID_SHAPE")

    def test_too_many_rows(self):
        payload = "[" + ",".join('["cash", 1]' for _ in range(25)) + "]"
        with self.assertRaises(PaymentSplitsParseError) as ctx:
            parse_payment_splits_json(payload, allowed_codes=frozenset({"cash"}), max_rows=24)
        self.assertEqual(ctx.exception.code, "TOO_MANY_ROWS")

    def test_invalid_method_strict(self):
        with self.assertRaises(PaymentSplitsParseError) as ctx:
            parse_payment_splits_json('[["unknown", 1]]', allowed_codes=frozenset({"cash"}))
        self.assertEqual(ctx.exception.code, "INVALID_METHOD")

    def test_invalid_method_lenient(self):
        lines = parse_payment_splits_json(
            '[["unknown", 1], ["cash", "2"]]',
            allowed_codes=frozenset({"cash"}),
            skip_invalid_methods=True,
        )
        self.assertEqual(lines, [("cash", Decimal("2"))])

    def test_empty_code_strict(self):
        with self.assertRaises(PaymentSplitsParseError) as ctx:
            parse_payment_splits_json('[["", 5]]', allowed_codes=frozenset({"cash"}))
        self.assertEqual(ctx.exception.code, "INVALID_METHOD")

    def test_invalid_amount_strict(self):
        with self.assertRaises(PaymentSplitsParseError) as ctx:
            parse_payment_splits_json('[["cash", "x"]]', allowed_codes=frozenset({"cash"}))
        self.assertEqual(ctx.exception.code, "INVALID_AMOUNT")

    def test_comma_decimal_separator(self):
        lines = parse_payment_splits_json(
            '[["cash", "12,5"]]',
            allowed_codes=frozenset({"cash"}),
            quantize=True,
        )
        self.assertEqual(lines[0][1], Decimal("12.50"))

    def test_without_quantize_preserves_scale(self):
        lines = parse_payment_splits_json(
            '[["cash", 14]]',
            allowed_codes=frozenset({"cash"}),
        )
        self.assertEqual(lines[0][1], Decimal("14"))
