"""اختبارات إخفاء واجهة الورديات/المستمر حسب نمط العمل."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from apps.core.models import PosSettings
from apps.core.operation_mode import MODE_CONTINUOUS, MODE_SHIFTS


class OperationModeUITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ui_mode", password="pass-12345")
        self.client = Client()
        self.client.login(username="ui_mode", password="pass-12345")

    def _set_mode(self, mode: str):
        PosSettings.objects.update_or_create(pk=1, defaults={"operation_mode": mode})

    def test_dashboard_hides_shifts_in_continuous(self):
        self._set_mode(MODE_CONTINUOUS)
        resp = self.client.get(reverse("shell:reports"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertNotIn("الورديات</h2>", html)
        self.assertIn("الصناديق (محاسبة مستمرة)", html)

    def test_dashboard_shows_shifts_in_shift_mode(self):
        self._set_mode(MODE_SHIFTS)
        resp = self.client.get(reverse("shell:reports"))
        html = resp.content.decode()
        self.assertIn("الورديات</h2>", html)
        self.assertNotIn("الصناديق (محاسبة مستمرة)", html)

    def test_settings_hides_channel_balances_tab_in_shifts(self):
        self._set_mode(MODE_SHIFTS)
        resp = self.client.get(reverse("shell:settings"))
        html = resp.content.decode()
        self.assertNotIn('data-tab="channel-balances"', html)

    def test_settings_shows_channel_balances_tab_in_continuous(self):
        self._set_mode(MODE_CONTINUOUS)
        resp = self.client.get(reverse("shell:settings"))
        html = resp.content.decode()
        self.assertIn('data-tab="channel-balances"', html)

    def test_payment_boxes_continuous_opening_note(self):
        self._set_mode(MODE_CONTINUOUS)
        resp = self.client.get(reverse("shell:payment_boxes"))
        self.assertContains(resp, "أرصدة الصناديق")
        self.assertNotContains(resp, "أول وردية")
