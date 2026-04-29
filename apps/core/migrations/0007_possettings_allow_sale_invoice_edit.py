from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_paymentmethod_remove_json"),
    ]

    operations = [
        migrations.AddField(
            model_name="possettings",
            name="allow_sale_invoice_edit",
            field=models.BooleanField(
                default=False,
                help_text="عند التفعيل يظهر رابط «تعديل الفاتورة» في تفاصيل الفاتورة وفي الكاشير. لا يُسمح بالتعديل إن وُجدت دفعة آجل، أو مرتجع على الفاتورة. يُحدَّث المخزون وفق فرق الكميات؛ دفعة واحدة غير الآجل تُعدَّل تلقائياً إذا تغيّر الإجمالي.",
                verbose_name="السماح بتعديل فاتورة البيع بعد الإصدار",
            ),
        ),
    ]
