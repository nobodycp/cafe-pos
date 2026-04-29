from decimal import Decimal

from django.test import TestCase

from apps.core.decimalutil import as_decimal


class AsDecimalTests(TestCase):
    def test_none_is_zero(self):
        self.assertEqual(as_decimal(None), Decimal("0"))

    def test_decimal_passthrough(self):
        d = Decimal("12.34")
        self.assertIs(as_decimal(d), d)

    def test_string_number(self):
        self.assertEqual(as_decimal("10.5"), Decimal("10.5"))
