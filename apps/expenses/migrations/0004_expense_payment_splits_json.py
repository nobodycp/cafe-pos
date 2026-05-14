from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("expenses", "0003_alter_expense_payment_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="expense",
            name="payment_splits_json",
            field=models.TextField(blank=True, default="", verbose_name="تقسيم الدفع (JSON)"),
        ),
    ]
