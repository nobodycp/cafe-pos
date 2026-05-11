# Generated manually for explicit «بائع نسبة» flag on suppliers.

from django.db import migrations, models


def forwards_fill_from_products(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Supplier = apps.get_model("purchasing", "Supplier")
    ids = (
        Product.objects.filter(commission_vendor_id__isnull=False)
        .values_list("commission_vendor_id", flat=True)
        .distinct()
    )
    Supplier.objects.filter(pk__in=list(ids)).update(is_commission_vendor=True)


class Migration(migrations.Migration):

    dependencies = [
        ("purchasing", "0005_alter_supplierpayment_method"),
        ("catalog", "0005_product_barcode"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="is_commission_vendor",
            field=models.BooleanField(
                default=False,
                help_text="يُعرض في قائمة الموردين؛ منتجات «عمولة» تختاره كبائع نسبة من شاشة المنتج.",
                verbose_name="بائع نسبة",
            ),
        ),
        migrations.RunPython(forwards_fill_from_products, migrations.RunPython.noop),
    ]
