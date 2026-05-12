# Generated manually for manufacturing batches and PRODUCTION movement type

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0002_stocktake_stocktakeline"),
    ]

    operations = [
        migrations.AlterField(
            model_name="stockmovement",
            name="movement_type",
            field=models.CharField(
                choices=[
                    ("purchase", "شراء"),
                    ("sale", "بيع"),
                    ("manufacturing", "استهلاك تصنيع"),
                    ("production", "إنتاج / تجميع"),
                    ("adjustment", "تسوية يدوية"),
                    ("waste", "هالك / تلف"),
                ],
                max_length=20,
                verbose_name="نوع الحركة",
            ),
        ),
        migrations.CreateModel(
            name="ManufacturingBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="أنشئ في")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="عُدّل في")),
                ("quantity", models.DecimalField(decimal_places=4, max_digits=18, verbose_name="كمية الإنتاج")),
                ("note", models.TextField(blank=True, verbose_name="ملاحظة")),
                (
                    "product",
                    models.ForeignKey(
                        limit_choices_to={"product_type": "manufactured"},
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="manufacturing_batches",
                        to="catalog.product",
                        verbose_name="المنتج المصنع",
                    ),
                ),
                (
                    "work_session",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="core.worksession",
                        verbose_name="الوردية",
                    ),
                ),
            ],
            options={
                "verbose_name": "دفعة تصنيع",
                "verbose_name_plural": "دفعات التصنيع",
                "ordering": ("-created_at",),
            },
        ),
    ]
