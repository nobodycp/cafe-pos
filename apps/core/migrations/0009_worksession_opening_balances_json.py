# Generated manually for per-channel opening balances.

from decimal import Decimal

from django.db import migrations, models


def backfill_opening_balances(apps, schema_editor):
    WorkSession = apps.get_model("core", "WorkSession")
    for ws in WorkSession.objects.all():
        data = ws.opening_balances_json
        if data:
            continue
        oc = ws.opening_cash
        if oc is None:
            oc = Decimal("0")
        ws.opening_balances_json = {"cash": str(Decimal(oc).quantize(Decimal("0.01")))}
        ws.save(update_fields=["opening_balances_json"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_possettings_receipt_thermal_branding"),
    ]

    operations = [
        migrations.AddField(
            model_name="worksession",
            name="opening_balances_json",
            field=models.JSONField(
                default=dict,
                blank=True,
                verbose_name="أرصدة افتتاحية لطرق الدفع",
                help_text="مفتاح = رمز طريقة الدفع، قيمة = المبلغ كنص عشري (مثل «5000.00» للكاش).",
            ),
        ),
        migrations.RunPython(backfill_opening_balances, migrations.RunPython.noop),
    ]
