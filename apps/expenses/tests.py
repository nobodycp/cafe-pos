import json
from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.expenses.forms import ExpenseForm
from apps.expenses.models import ExpenseCategory


class ExpenseFormPaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = ExpenseCategory.objects.create(
            code="misc",
            name_ar="متفرقات",
            name_en="Misc",
        )

    def _base_data(self, **overrides):
        data = {
            "category": str(self.category.pk),
            "amount": "100.00",
            "expense_date": date.today().isoformat(),
            "notes": "",
            "payment_method": "",
            "payment_splits_json": "",
        }
        data.update(overrides)
        return data

    def test_single_payment_method_valid(self):
        form = ExpenseForm(self._base_data(payment_method="cash"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["payment_method"], "cash")

    def test_missing_payment_method_invalid(self):
        form = ExpenseForm(self._base_data())
        self.assertFalse(form.is_valid())
        self.assertIn("payment_method", form.errors)

    def test_split_payment_valid(self):
        splits = json.dumps([["cash", "60"], ["bank_ps", "40"]], ensure_ascii=False)
        form = ExpenseForm(
            self._base_data(
                use_payment_splits="on",
                payment_splits_json=splits,
                payment_method="",
            )
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["payment_method"], "split")
        self.assertIn("cash", form.cleaned_data["payment_splits_json"])
