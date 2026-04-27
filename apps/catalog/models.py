from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Unit(TimeStampedModel):
    code = models.SlugField(_("الرمز"), max_length=32, unique=True)
    name_ar = models.CharField(_("الاسم (عربي)"), max_length=128)
    name_en = models.CharField(_("الاسم (إنجليزي)"), max_length=128, blank=True)

    class Meta:
        verbose_name = _("وحدة قياس")
        verbose_name_plural = _("وحدات القياس")
        ordering = ("code",)

    def __str__(self):
        return self.name_ar

    def display(self, lang: str = "ar") -> str:
        if lang == "en" and self.name_en:
            return self.name_en
        return self.name_ar


class Category(TimeStampedModel):
    parent = models.ForeignKey(
        "self",
        verbose_name=_("التصنيف الرئيسي"),
        null=True,
        blank=True,
        related_name="children",
        on_delete=models.CASCADE,
    )
    name_ar = models.CharField(_("الاسم (عربي)"), max_length=160)
    name_en = models.CharField(_("الاسم (إنجليزي)"), max_length=160, blank=True)
    sort_order = models.PositiveIntegerField(_("الترتيب"), default=0)
    is_active = models.BooleanField(_("نشط"), default=True)

    class Meta:
        verbose_name = _("تصنيف")
        verbose_name_plural = _("التصنيفات")
        ordering = ("sort_order", "name_ar")

    def __str__(self):
        return self.name_ar

    def display(self, lang: str = "ar") -> str:
        if lang == "en" and self.name_en:
            return self.name_en
        return self.name_ar


class Product(TimeStampedModel):
    class ProductType(models.TextChoices):
        RAW = "raw", _("مادة خام")
        READY = "ready", _("منتج جاهز")
        MANUFACTURED = "manufactured", _("منتج مصنع")
        SERVICE = "service", _("خدمة")
        COMMISSION = "commission", _("عمولة / وسيط")

    category = models.ForeignKey(
        Category,
        verbose_name=_("التصنيف"),
        related_name="products",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    unit = models.ForeignKey(
        Unit,
        verbose_name=_("الوحدة"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    name_ar = models.CharField(_("الاسم (عربي)"), max_length=200)
    name_en = models.CharField(_("الاسم (إنجليزي)"), max_length=200, blank=True)
    selling_price = models.DecimalField(_("سعر البيع"), max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    product_type = models.CharField(_("نوع المنتج"), max_length=20, choices=ProductType.choices)
    is_stock_tracked = models.BooleanField(_("تتبع المخزون؟"), default=False)
    commission_percentage = models.DecimalField(
        _("نسبة العمولة %"),
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    commission_vendor = models.ForeignKey(
        "purchasing.Supplier",
        verbose_name=_("بائع النسبة"),
        null=True,
        blank=True,
        related_name="commission_products",
        on_delete=models.SET_NULL,
        help_text=_("المورد/البائع صاحب المنتج الذي نبيعه بالنسبة"),
    )
    min_stock_level = models.DecimalField(_("الحد الأدنى للمخزون"), max_digits=14, decimal_places=3, default=0)
    is_active = models.BooleanField(_("نشط"), default=True)
    barcode = models.CharField(_("الباركود"), max_length=128, blank=True, db_index=True)

    class Meta:
        verbose_name = _("منتج")
        verbose_name_plural = _("المنتجات")
        ordering = ("name_ar",)

    def __str__(self):
        return self.name_ar

    def display(self, lang: str = "ar") -> str:
        if lang == "en" and self.name_en:
            return self.name_en
        return self.name_ar

    @property
    def has_recipe(self) -> bool:
        if self.product_type != self.ProductType.MANUFACTURED:
            return False
        return self.recipe_lines.exists()


class RecipeLine(TimeStampedModel):
    """كميات المكوّنات لكل 1 وحدة من المنتج المصنع."""

    manufactured_product = models.ForeignKey(
        Product,
        verbose_name=_("المنتج المصنع"),
        related_name="recipe_lines",
        on_delete=models.CASCADE,
        limit_choices_to={"product_type": Product.ProductType.MANUFACTURED},
    )
    component = models.ForeignKey(
        Product,
        verbose_name=_("المكوّن"),
        related_name="used_in_recipes",
        on_delete=models.PROTECT,
    )
    quantity_per_unit = models.DecimalField(
        _("الكمية لكل وحدة منتج"),
        max_digits=14,
        decimal_places=4,
        validators=[MinValueValidator(0)],
    )

    class Meta:
        verbose_name = _("سطر وصفة")
        verbose_name_plural = _("مكوّنات الوصفات")
        unique_together = ("manufactured_product", "component")

    def __str__(self):
        return f"{self.manufactured_id}: {self.component_id} x {self.quantity_per_unit}"


class ProductModifierGroup(TimeStampedModel):
    product = models.ForeignKey(
        Product,
        verbose_name=_("المنتج"),
        related_name="modifier_groups",
        on_delete=models.CASCADE,
    )
    name_ar = models.CharField(_("اسم المجموعة"), max_length=120)
    name_en = models.CharField(_("الاسم (إنجليزي)"), max_length=120, blank=True)
    min_select = models.PositiveSmallIntegerField(_("حد أدنى للاختيار"), default=0)
    max_select = models.PositiveSmallIntegerField(_("حد أقصى للاختيار"), default=1)
    sort_order = models.PositiveIntegerField(_("الترتيب"), default=0)

    class Meta:
        verbose_name = _("مجموعة معدّلات")
        verbose_name_plural = _("مجموعات المعدّلات")
        ordering = ("sort_order", "id")

    def __str__(self):
        return f"{self.product_id}: {self.name_ar}"


class ProductModifierOption(TimeStampedModel):
    group = models.ForeignKey(
        ProductModifierGroup,
        verbose_name=_("المجموعة"),
        related_name="options",
        on_delete=models.CASCADE,
    )
    name_ar = models.CharField(_("الخيار"), max_length=120)
    name_en = models.CharField(_("الخيار (إنجليزي)"), max_length=120, blank=True)
    price_delta = models.DecimalField(
        _("تغيير السعر"),
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    sort_order = models.PositiveIntegerField(_("الترتيب"), default=0)

    class Meta:
        verbose_name = _("خيار معدّل")
        verbose_name_plural = _("خيارات المعدّلات")
        ordering = ("sort_order", "id")

    def __str__(self):
        return self.name_ar
