# Generated manually — توسيع طرق سداد المورد.

from django.db import migrations, models


def forwards_bank_to_bank_ps(apps, schema_editor):
    SupplierPayment = apps.get_model("purchasing", "SupplierPayment")
    SupplierPayment.objects.filter(method="bank").update(method="bank_ps")


def backwards_merge(apps, schema_editor):
    SupplierPayment = apps.get_model("purchasing", "SupplierPayment")
    for m in ("bank_ps", "palpay", "jawwalpay", "credit"):
        SupplierPayment.objects.filter(method=m).update(method="bank")


class Migration(migrations.Migration):

    dependencies = [
        ("purchasing", "0003_purchasereturn_purchasereturnline"),
    ]

    operations = [
        migrations.AlterField(
            model_name="supplierpayment",
            name="method",
            field=models.CharField(
                choices=[
                    ("cash", "كاش"),
                    ("bank", "شبكة (عام)"),
                    ("bank_ps", "بنك فلسطين"),
                    ("palpay", "بال باي"),
                    ("jawwalpay", "جوال باي"),
                    ("credit", "آجل / من الرصيد"),
                ],
                max_length=20,
                verbose_name="طريقة الدفع",
            ),
        ),
        migrations.RunPython(forwards_bank_to_bank_ps, backwards_merge),
    ]
