from django import forms

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
        from apps.purchasing.models import Supplier
        self.fields["commission_vendor"].queryset = Supplier.objects.filter(is_active=True)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-input")


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["code", "name_ar", "name_en"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-input")


class RecipeLineForm(forms.ModelForm):
    class Meta:
        model = RecipeLine
        fields = ["component", "quantity_per_unit"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["component"].queryset = Product.objects.filter(is_active=True)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-input")
