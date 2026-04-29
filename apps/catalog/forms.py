from django import forms
from decimal import Decimal

from apps.catalog.models import Category, Product, RecipeLine, Unit


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name_ar", "name_en", "parent", "sort_order", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-input")


class ProductForm(forms.ModelForm):
    SELLABLE_TYPES = [
        (Product.ProductType.READY, "منتج جاهز"),
        (Product.ProductType.MANUFACTURED, "منتج مصنع"),
        (Product.ProductType.SERVICE, "خدمة"),
        (Product.ProductType.COMMISSION, "عمولة / وسيط"),
    ]

    class Meta:
        model = Product
        fields = [
            "name_ar",
            "name_en",
            "category",
            "unit",
            "selling_price",
            "product_type",
            "is_stock_tracked",
            "commission_percentage",
            "commission_vendor",
            "min_stock_level",
            "is_active",
            "barcode",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product_type"].choices = self.SELLABLE_TYPES
        self.fields["category"].required = False
        self.fields["min_stock_level"].required = False
        from apps.purchasing.models import Supplier
        self.fields["commission_vendor"].queryset = Supplier.objects.filter(is_active=True)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-input")

    def clean(self):
        cleaned = super().clean()
        product_type = cleaned.get("product_type")
        if product_type in (Product.ProductType.MANUFACTURED, Product.ProductType.SERVICE, Product.ProductType.COMMISSION):
            cleaned["is_stock_tracked"] = False
            cleaned["min_stock_level"] = Decimal("0")
        return cleaned


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["code", "name_ar", "name_en"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-input")


class QuickCategoryForm(forms.ModelForm):
    """تصنيف سريع من شاشة إعداد المنتج الموحّدة."""

    class Meta:
        model = Category
        fields = ["name_ar", "name_en"]
        widgets = {
            "name_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "مثال: مشروبات ساخنة"}),
            "name_en": forms.TextInput(attrs={"class": "form-input", "placeholder": "Optional"}),
        }


class QuickUnitForm(forms.ModelForm):
    """وحدة قياس سريعة من شاشة إعداد المنتج الموحّدة."""

    class Meta:
        model = Unit
        fields = ["code", "name_ar", "name_en"]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-input", "placeholder": "مثال: pcs", "dir": "ltr"}),
            "name_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "قطعة، كغ، صحن..."}),
            "name_en": forms.TextInput(attrs={"class": "form-input"}),
        }


class RecipeLineForm(forms.ModelForm):
    class Meta:
        model = RecipeLine
        fields = ["component", "quantity_per_unit"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["component"].queryset = Product.objects.filter(is_active=True)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-input")
