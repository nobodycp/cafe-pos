from django.contrib.auth.models import User
from django.test import TestCase

from apps.core.models import AuditLog
from apps.core.treasury_services import TREASURY_VOUCHER_AUDIT_ACTION
from apps.core.treasury_void import void_unified_treasury_voucher


class TreasuryVoidCustomerMissingTests(TestCase):
    def test_void_receipt_customer_succeeds_when_customer_and_entry_missing(self):
        user = User.objects.create_user(username="void_user", password="x")
        log = AuditLog.objects.create(
            user=user,
            action=TREASURY_VOUCHER_AUDIT_ACTION,
            model_label="treasury.UnifiedVoucher",
            object_pk="",
            payload={
                "voucher_type": "receipt",
                "party_type": "customer",
                "customer_pk": 999001,
                "ledger_entry_pk": 999002,
                "amount": "10.00",
                "method": "cash",
                "voucher_date": "2026-05-21",
            },
        )

        void_unified_treasury_voucher(audit_log_id=log.pk, user=user)

        log.refresh_from_db()
        self.assertTrue(log.payload.get("cancelled"))
