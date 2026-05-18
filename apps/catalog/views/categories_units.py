from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
import json
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

from apps.catalog.forms import (
    PRODUCT_QUICK_FORM_PREFIX,
    CategoryForm,
    ProductForm,
    QuickCategoryForm,
    QuickUnitForm,
    RecipeLineForm,
    UnitForm,
)
from apps.catalog.models import Category, Product, RecipeLine, Unit
from apps.catalog.product_list_filters import (
    CATEGORY_SORT_CHOICES,
    ACTIVE_FILTER_CHOICES,
    PARENT_FILTER_CHOICES,
    PRODUCT_SORT_CHOICES,
    STOCK_FILTER_CHOICES,
    UNIT_SORT_CHOICES,
    apply_category_filters,
    apply_product_filters,
    apply_unit_filters,
    categories_filters_open,
    category_filter_options,
    parent_category_options,
    parse_category_filters,
    parse_product_filters,
    parse_unit_filters,
    products_filters_open,
    units_filters_open,
)
from apps.core.models import log_audit
from apps.core.panel import PanelFormInvalid, handle_panel_form, panelize_form
from apps.core.services import SessionService
from apps.inventory.models import ManufacturingBatch, StockBalance, StockMovement, StockTakeLine
from apps.inventory.services import get_unit_cost, record_manufacturing_batch, void_manufacturing_batch
from apps.billing.models import SaleInvoiceLine
from apps.core.pagination import paginate_queryset
from apps.pos.models import Order, OrderLine
from apps.purchasing.models import PurchaseLine


WEIGHT_UNIT_CODES = {"kg", "kilo", "kilogram", "كيلو", "كيلوغرام"}
VOLUME_UNIT_CODES = {"l", "lt", "ltr", "liter", "litre", "lter", "لتر"}



from apps.catalog.views._helpers import _catalog_ctx, _catalog_redirect, _catalog_reverse

def category_create(request):
    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إضافة التصنيف بنجاح")
            return _catalog_redirect(request, "category_list")
    else:
        form = CategoryForm()
    return render(request, "shell/category_form.html", _catalog_ctx(request, form=form))


def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if Product.objects.filter(category=category).exists():
        messages.error(request, "لا يمكن حذف التصنيف: توجد منتجات مرتبطة به.")
        return _catalog_redirect(request, "product_list")
    if Category.objects.filter(parent=category).exists():
        messages.error(request, "لا يمكن حذف التصنيف: توجد تصنيفات فرعية مرتبطة به.")
        return _catalog_redirect(request, "product_list")
    name = category.name_ar
    category.delete()
    messages.success(request, f"تم حذف التصنيف «{name}».")
    return _catalog_redirect(request, "product_list")


def category_edit(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل التصنيف بنجاح")
            return _catalog_redirect(request, "category_list")
    else:
        form = CategoryForm(instance=category)
    return render(request, "shell/category_form.html", _catalog_ctx(request, form=form))


def category_list(request):
    params = request.GET.copy()
    params["tab"] = "categories"
    return redirect(f"{reverse('shell:product_list')}?{params.urlencode()}")


def unit_create(request):
    if request.method == "POST":
        form = UnitForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إضافة الوحدة بنجاح")
            return _catalog_redirect(request, "unit_list")
    else:
        form = UnitForm()
    return render(request, "shell/unit_form.html", _catalog_ctx(request, form=form))


def unit_delete(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    if Product.objects.filter(unit=unit).exists():
        messages.error(request, "لا يمكن حذف الوحدة: توجد منتجات مرتبطة بها.")
        return _catalog_redirect(request, "product_list")
    name = unit.name_ar
    unit.delete()
    messages.success(request, f"تم حذف الوحدة «{name}».")
    return _catalog_redirect(request, "product_list")


def unit_edit(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    if request.method == "POST":
        form = UnitForm(request.POST, instance=unit)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل الوحدة بنجاح")
            return _catalog_redirect(request, "unit_list")
    else:
        form = UnitForm(instance=unit)
    return render(request, "shell/unit_form.html", _catalog_ctx(request, form=form))


def unit_list(request):
    params = request.GET.copy()
    params["tab"] = "units"
    return redirect(f"{reverse('shell:product_list')}?{params.urlencode()}")
