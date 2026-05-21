"""ربط طرق الدفع cash/bank بحسابات فرعية في دليل الحسابات."""

from django.db import migrations


def forward(apps, schema_editor):
    from apps.core.gl_accounts import ensure_all_payment_method_gl_accounts

    ensure_all_payment_method_gl_accounts()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_operation_mode_continuous"),
        ("accounting", "0006_seed_discount_voucher_accounts"),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
