from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pos", "0002_pos_advanced"),
    ]

    operations = [
        migrations.AddField(
            model_name="diningtable",
            name="ephemeral",
            field=models.BooleanField(
                default=False,
                verbose_name="مؤقتة من الكاشير",
                help_text="إن كانت صحيحة تُلغى الطاولة تلقائياً عند إغلاق جلسة الطاولة (لا تبقى طاولات ثابتة).",
            ),
        ),
    ]
