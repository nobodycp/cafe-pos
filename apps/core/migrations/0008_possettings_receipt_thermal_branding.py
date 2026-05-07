from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_possettings_allow_sale_invoice_edit"),
    ]

    operations = [
        migrations.AddField(
            model_name="possettings",
            name="receipt_logo_url",
            field=models.CharField(
                blank=True,
                max_length=500,
                verbose_name="رابط شعار الإيصال الحراري",
                help_text="مسار كامل أو نسبي (مثل /static/pos/logo.png) يُعرض أعلى الإيصال.",
            ),
        ),
        migrations.AddField(
            model_name="possettings",
            name="receipt_slogan_ar",
            field=models.CharField(
                blank=True,
                max_length=300,
                verbose_name="شعار ترويجي على الإيصال (عربي)",
                help_text="سطر قبل صندوق الهاتف والعنوان (مثل: جودة وعروض على طول).",
            ),
        ),
        migrations.AddField(
            model_name="possettings",
            name="receipt_stamp_text",
            field=models.CharField(
                blank=True,
                max_length=240,
                verbose_name="نص الختم على الإيصال",
                help_text="أسطر تفصلها فاصلة منقوطة (;) لتظهر داخل الختم المائل.",
            ),
        ),
    ]
