from django.contrib import admin

from apps.catalog.models import (
    Category,
    Product,
    ProductModifierGroup,
    ProductModifierOption,
    RecipeLine,
    Unit,
)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "name_en")


class RecipeLineInline(admin.TabularInline):
    model = RecipeLine
    fk_name = "manufactured_product"
    extra = 0


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name_ar", "parent", "sort_order", "is_active")


class ProductModifierOptionInline(admin.TabularInline):
    model = ProductModifierOption
    extra = 0


class ProductModifierGroupInline(admin.TabularInline):
    model = ProductModifierGroup
    extra = 0


@admin.register(ProductModifierGroup)
class ProductModifierGroupAdmin(admin.ModelAdmin):
    list_display = ("product", "name_ar", "min_select", "max_select", "sort_order")
    inlines = [ProductModifierOptionInline]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name_ar", "category", "product_type", "selling_price", "is_stock_tracked", "is_active")
    list_filter = ("product_type", "is_active", "is_stock_tracked")
    inlines = [RecipeLineInline, ProductModifierGroupInline]
