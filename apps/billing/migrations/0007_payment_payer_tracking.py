from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0006_alter_invoicepayment_method_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoicepayment",
            name="payer_name",
            field=models.CharField(blank=True, default="", max_length=120, verbose_name="اسم المحوّل (تتبع)"),
        ),
        migrations.AddField(
            model_name="invoicepayment",
            name="payer_phone",
            field=models.CharField(blank=True, default="", max_length=40, verbose_name="جوال المحوّل (تتبع)"),
        ),
        migrations.AddField(
            model_name="invoicepayment",
            name="payment_source",
            field=models.CharField(
                blank=True,
                default="",
                max_length=24,
                verbose_name="مصدر الدفعة",
                help_text="table / takeaway / delivery — للتقارير",
            ),
        ),
        migrations.AddField(
            model_name="orderpayment",
            name="payer_name",
            field=models.CharField(blank=True, default="", max_length=120, verbose_name="اسم المحوّل (تتبع)"),
        ),
        migrations.AddField(
            model_name="orderpayment",
            name="payer_phone",
            field=models.CharField(blank=True, default="", max_length=40, verbose_name="جوال المحوّل (تتبع)"),
        ),
        migrations.AddField(
            model_name="orderpayment",
            name="payment_source",
            field=models.CharField(
                blank=True,
                default="",
                max_length=24,
                verbose_name="مصدر الدفعة",
                help_text="table / takeaway / delivery",
            ),
        ),
    ]
