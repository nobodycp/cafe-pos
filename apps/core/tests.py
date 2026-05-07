from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.core.decimalutil import as_decimal


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
