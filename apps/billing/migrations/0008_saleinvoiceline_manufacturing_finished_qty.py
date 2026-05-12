from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0007_payment_payer_tracking"),
    ]

    operations = [
        migrations.AddField(
            model_name="saleinvoiceline",
            name="manufacturing_finished_qty",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                help_text="للمنتج المصنع المتتبع: جزء السطر المسحوب من مخزون الجاهز (الباقي من المواد حسب المعادلة).",
                max_digits=14,
                null=True,
                verbose_name="كمية مباعة من الرصيد الجاهز",
            ),
        ),
    ]
