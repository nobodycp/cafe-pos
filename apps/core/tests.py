import re
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.core.decimalutil import as_decimal
from apps.core.models import WorkSession
from apps.expenses.models import Expense, ExpenseCategory
from apps.purchasing.models import Supplier, SupplierPayment


class AsDecimalTests(TestCase):
    def test_none_is_zero(self):
        self.assertEqual(as_decimal(None), Decimal("0"))

    def test_decimal_passthrough(self):
        d = Decimal("12.34")
        self.assertIs(as_decimal(d), d)

    def test_string_number(self):
        self.assertEqual(as_decimal("10.5"), Decimal("10.5"))


class ShellRoutesAuthTests(TestCase):
    """Smoke tests for shell URLs and login gate — guards regressions in shell_urls composition."""

    def setUp(self):
        self.user = User.objects.create_user(username="t_shell", password="pass-12345")

    def test_anonymous_redirects_from_shell_settings(self):
        url = reverse("shell:settings")
        resp = self.client.get(url, follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_authenticated_settings_ok(self):
        self.client.login(username="t_shell", password="pass-12345")
        resp = self.client.get(reverse("shell:settings"))
        self.assertEqual(resp.status_code, 200)

    def test_shell_reverse_accounting_chart(self):
        self.assertEqual(reverse("shell:accounting_chart"), "/app/accounting/accounts/")


class NavBackTests(TestCase):
    def test_safe_return_path_rejects_external(self):
        from apps.core.nav_back import safe_return_path

        self.assertEqual(safe_return_path("/app/reports/product-movement/"), "/app/reports/product-movement/")
        self.assertEqual(safe_return_path("//evil.test/"), "")
        self.assertEqual(safe_return_path("https://evil.test/"), "")

    def test_append_return_preserves_filters(self):
        from django.test import RequestFactory

        from apps.core.nav_back import append_return

        rf = RequestFactory()
        req = rf.get("/app/reports/product-movement/?period=month")
        url = append_return(reverse("shell:product_card", args=[1]), req)
        self.assertIn("return=", url)
        self.assertIn("product-movement", url)

    def test_chart_back_ignores_ledger_referer(self):
        from django.test import RequestFactory

        from apps.core.nav_back import toolbar_back_for_request

        self.user = User.objects.create_user(username="nav2", password="x")
        rf = RequestFactory()
        req = rf.get(
            "/app/accounting/accounts/",
            HTTP_REFERER="http://testserver/app/accounting/accounts/9/ledger/",
        )
        req.user = self.user
        req.resolver_match = type(
            "M",
            (),
            {"namespace": "shell", "url_name": "accounting_chart", "kwargs": {}},
        )()
        ctx = toolbar_back_for_request(req)
        self.assertEqual(ctx["toolbar_back_url"], reverse("pos:main"))
        self.assertNotIn("/ledger/", ctx["toolbar_back_url"])

    def test_product_card_back_from_return_param(self):
        from django.test import RequestFactory

        from apps.core.nav_back import toolbar_back_for_request

        self.user = User.objects.create_user(username="nav", password="x")
        rf = RequestFactory()
        req = rf.get(
            "/app/products/1/card/?return=/app/reports/product-movement/%3Fperiod%3Dmonth"
        )
        req.user = self.user
        req.resolver_match = type(
            "M",
            (),
            {"namespace": "shell", "url_name": "product_card", "kwargs": {"pk": 1}},
        )()
        ctx = toolbar_back_for_request(req)
        self.assertEqual(ctx["toolbar_back_url"], "/app/reports/product-movement/?period=month")
        self.assertEqual(ctx["toolbar_back_label"], "← رجوع")


class SessionSummarySupplierPaymentTests(TestCase):
    """سند صرف لمورد يجب أن يظهر في عمود مصروفات مطابقة الصناديق عند إغلاق الوردية."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="shift_close", password="pass-12345")
        cls.category = ExpenseCategory.objects.create(
            code="other",
            name_ar="أخرى",
            name_en="Other",
        )
        cls.supplier = Supplier.objects.create(name_ar="تاجر تجريبي")

    def setUp(self):
        WorkSession.objects.filter(status=WorkSession.Status.OPEN).update(status=WorkSession.Status.CLOSED)
        self.ws = WorkSession.objects.create(
            opened_by=self.user,
            opening_cash=Decimal("0"),
            opening_balances_json={"cash": "0", "bank_ps": "0"},
        )

    def test_supplier_disbursement_in_desk_reconcile_expenses(self):
        Expense.objects.create(
            work_session=self.ws,
            category=self.category,
            expense_date=date.today(),
            amount=Decimal("86"),
            payment_method="bank_ps",
        )
        SupplierPayment.objects.create(
            supplier=self.supplier,
            work_session=self.ws,
            amount=Decimal("1600"),
            method="bank_ps",
            note="سند صرف",
        )
        self.client.login(username="shift_close", password="pass-12345")
        resp = self.client.get(reverse("core:session_summary"))
        self.assertEqual(resp.status_code, 200)
        row = next(r for r in resp.context["desk_reconcile_rows"] if r["code"] == "bank_ps")
        self.assertEqual(row["expenses"], Decimal("1686.00"))
        self.assertEqual(row["expected"], Decimal("-1686.00"))

    def test_desk_reconcile_table_column_alignment_markup(self):
        """عناوين وأرقام الجدول يجب أن تشترك في col-num/col-label لتجاوز محاذاة th في app.css."""
        self.client.login(username="shift_close", password="pass-12345")
        html = self.client.get(reverse("core:session_summary")).content.decode()
        self.assertIn('id="session-reconcile-table"', html)
        self.assertIn("#session-reconcile-table.session-reconcile-table th.col-num", html)
        table_html = html.split('id="session-reconcile-table"', 1)[1].split("</table>", 1)[0]
        th_classes = re.findall(r"<th class=\"([^\"]+)\"", table_html)
        self.assertGreaterEqual(len(th_classes), 5)
        self.assertTrue(th_classes[0].startswith("col-label"))
        self.assertTrue(all(c.startswith("col-num") for c in th_classes[1:5]))
        tbody_html = table_html.split("<tbody", 1)[1]
        td_classes = re.findall(r"<td class=\"([^\"]+)\"", tbody_html)
        self.assertGreaterEqual(len(td_classes), 5)
        self.assertTrue(td_classes[0].startswith("col-label"))
        self.assertTrue(all(c.startswith("col-num") for c in td_classes[1:5]))
