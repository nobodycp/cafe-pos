from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

PAYMENT_METHOD_CODE_VALIDATOR = RegexValidator(
    r"^[a-z][a-z0-9_]{0,31}$",
    _("رمز لاتيني صغير: يبدأ بحرف ثم أرقام أو _."),
)


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(_("أنشئ في"), auto_now_add=True)
    updated_at = models.DateTimeField(_("عُدّل في"), auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(TimeStampedModel):
    is_cancelled = models.BooleanField(_("ملغى"), default=False)
    cancelled_at = models.DateTimeField(_("تاريخ الإلغاء"), null=True, blank=True)
    cancel_reason = models.TextField(_("سبب الإلغاء"), blank=True)

    class Meta:
        abstract = True

    def soft_cancel(self, reason: str = ""):
        from django.utils import timezone

        self.is_cancelled = True
        self.cancelled_at = timezone.now()
        self.cancel_reason = reason
        self.save(update_fields=["is_cancelled", "cancelled_at", "cancel_reason", "updated_at"])


class WorkSession(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", _("مفتوحة")
        CLOSED = "closed", _("مغلقة")

    status = models.CharField(_("الحالة"), max_length=16, choices=Status.choices, default=Status.OPEN)
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("فتحها"),
        related_name="opened_work_sessions",
        on_delete=models.PROTECT,
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("أغلقها"),
        related_name="closed_work_sessions",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(_("وقت فتح الوردية"), auto_now_add=True)
    updated_at = models.DateTimeField(_("عُدّل في"), auto_now=True)
    closed_at = models.DateTimeField(_("وقت الإغلاق"), null=True, blank=True)
    opening_cash = models.DecimalField(_("الصندوق الافتتاحي"), max_digits=14, decimal_places=2, default=0)
    closing_cash = models.DecimalField(_("الصندوق الختامي"), max_digits=14, decimal_places=2, null=True, blank=True)
    notes = models.TextField(_("ملاحظات"), blank=True)

    class Meta:
        verbose_name = _("وردية عمل")
        verbose_name_plural = _("ورديات العمل")
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["status"],
                condition=models.Q(status="open"),
                name="unique_open_session",
            ),
        ]

    def __str__(self):
        return f"Session #{self.pk} ({self.get_status_display()})"


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("المستخدم"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    action = models.CharField(_("الإجراء"), max_length=64)
    model_label = models.CharField(_("النموذج"), max_length=128, blank=True)
    object_pk = models.CharField(_("معرف السجل"), max_length=64, blank=True)
    payload = models.JSONField(_("تفاصيل"), default=dict, blank=True)
    created_at = models.DateTimeField(_("الوقت"), auto_now_add=True)

    class Meta:
        verbose_name = _("سجل تدقيق")
        verbose_name_plural = _("سجلات التدقيق")
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.action} @ {self.created_at}"


class IdSequence(models.Model):
    """Atomic counters for document numbers (row locked per key)."""

    key = models.CharField(_("المفتاح"), max_length=64, primary_key=True)
    value = models.PositiveIntegerField(_("القيمة"), default=0)

    class Meta:
        verbose_name = _("تسلسل معرفات")
        verbose_name_plural = _("تسلسلات المعرفات")


class PaymentMethod(models.Model):
    """طرق الدفع/التحصيل — تظهر كأزرار في الكاشير والسندات والقوائم."""

    class Ledger(models.TextChoices):
        CASH = "cash", _("صندوق نقدي")
        BANK = "bank", _("بنك / شبكة")
        AR = "ar", _("آجل / ذمم")

    code = models.CharField(
        _("رمز النظام"),
        max_length=32,
        unique=True,
        validators=[PAYMENT_METHOD_CODE_VALIDATOR],
        help_text=_("لاتيني صغير، للاستخدام الداخلي (مثل cash، bank_ps)."),
    )
    label_ar = models.CharField(_("الاسم المعروض"), max_length=120)
    label_en = models.CharField(_("الاسم (إنجليزي)"), max_length=120, blank=True)
    ledger = models.CharField(
        _("نوع الحساب المحاسبي"),
        max_length=8,
        choices=Ledger.choices,
        default=Ledger.BANK,
    )
    is_active = models.BooleanField(_("نشط"), default=True)
    sort_order = models.PositiveSmallIntegerField(_("الترتيب"), default=0)

    class Meta:
        verbose_name = _("طريقة دفع")
        verbose_name_plural = _("طرق الدفع")
        ordering = ("sort_order", "pk")

    def __str__(self):
        return f"{self.label_ar} ({self.code})"


class PosSettings(models.Model):
    """Singleton row pk=1 — all system settings."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)

    # ── معلومات المقهى ──
    cafe_name_ar = models.CharField(_("اسم المقهى (عربي)"), max_length=200, blank=True)
    cafe_name_en = models.CharField(_("اسم المقهى (إنجليزي)"), max_length=200, blank=True)
    cafe_phone = models.CharField(_("هاتف المقهى"), max_length=32, blank=True)
    cafe_address = models.TextField(_("عنوان المقهى"), blank=True)
    cafe_tax_number = models.CharField(_("الرقم الضريبي"), max_length=64, blank=True)

    # ── العملة ──
    currency_symbol = models.CharField(_("رمز العملة"), max_length=8, default="ر.س")
    currency_code = models.CharField(_("كود العملة"), max_length=8, default="SAR")
    decimal_places = models.PositiveSmallIntegerField(_("خانات عشرية"), default=2)

    # ── الضريبة والخدمة ──
    default_tax_percent = models.DecimalField(
        _("ضريبة افتراضية %"),
        max_digits=6,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    default_service_charge_percent = models.DecimalField(
        _("خدمة افتراضية %"),
        max_digits=6,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    tax_included_in_price = models.BooleanField(_("الضريبة مشمولة في السعر"), default=False)

    # ── إعدادات الطلب ──
    default_order_type = models.CharField(
        _("نوع الطلب الافتراضي"),
        max_length=16,
        choices=[("dine_in", _("صالة")), ("takeaway", _("سفري")), ("delivery", _("توصيل"))],
        default="dine_in",
    )
    allow_negative_stock = models.BooleanField(_("السماح بمخزون سالب"), default=True)
    require_customer_for_credit = models.BooleanField(_("يجب تحديد عميل للبيع الآجل"), default=True)

    # ── الطابعات ──
    kitchen_auto_print = models.BooleanField(_("طباعة مطبخ تلقائية"), default=False)
    printer_kitchen_label = models.CharField(_("اسم طابعة المطبخ"), max_length=120, blank=True)
    printer_receipt_label = models.CharField(_("اسم طابعة الإيصال"), max_length=120, blank=True)

    # ── الإيصال ──
    receipt_header = models.TextField(_("رأس الإيصال (نص إضافي)"), blank=True)
    receipt_footer = models.TextField(_("تذييل الإيصال (نص إضافي)"), blank=True)
    receipt_logo_url = models.CharField(
        _("رابط شعار الإيصال الحراري"),
        max_length=500,
        blank=True,
        help_text=_("مسار كامل أو نسبي (مثل /static/pos/logo.png) يُعرض أعلى الإيصال."),
    )
    receipt_slogan_ar = models.CharField(
        _("شعار ترويجي على الإيصال (عربي)"),
        max_length=300,
        blank=True,
        help_text=_("سطر قبل صندوق الهاتف والعنوان (مثل: جودة وعروض على طول)."),
    )
    receipt_stamp_text = models.CharField(
        _("نص الختم على الإيصال"),
        max_length=240,
        blank=True,
        help_text=_("أسطر تفصلها فاصلة منقوطة (;) لتظهر داخل الختم المائل."),
    )
    receipt_show_tax_number = models.BooleanField(_("إظهار الرقم الضريبي على الإيصال"), default=True)
    allow_sale_invoice_edit = models.BooleanField(
        _("السماح بتعديل فاتورة البيع بعد الإصدار"),
        default=False,
        help_text=_(
            "عند التفعيل يظهر رابط «تعديل الفاتورة» في تفاصيل الفاتورة وفي الكاشير. "
            "لا يُسمح بالتعديل إن وُجدت دفعة آجل، أو مرتجع على الفاتورة. "
            "يُحدَّث المخزون وفق فرق الكميات؛ دفعة واحدة غير الآجل تُعدَّل تلقائياً إذا تغيّر الإجمالي."
        ),
    )

    class Meta:
        verbose_name = _("إعدادات النظام")
        verbose_name_plural = _("إعدادات النظام")

    def __str__(self):
        return "System Settings"

    @property
    def payment_method_rows(self):
        from apps.core import payment_methods

        return payment_methods.load_payment_method_rows()


def log_audit(user, action: str, model_label: str = "", object_pk: str = "", payload=None):
    AuditLog.objects.create(
        user=user if user and user.is_authenticated else None,
        action=action,
        model_label=model_label,
        object_pk=str(object_pk) if object_pk is not None else "",
        payload=payload or {},
    )


def get_pos_settings() -> "PosSettings":
    s, _ = PosSettings.objects.get_or_create(pk=1)
    return s
