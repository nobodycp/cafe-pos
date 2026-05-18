from django.views.decorators.http import require_GET

from apps.core.api.decorators import api_login_required
from apps.core.api.responses import json_ok
from apps.core.api.search_handlers import (
    search_accounts,
    search_categories,
    search_customers,
    search_sale_products,
    search_suppliers,
    search_units,
)
from apps.core.payment_methods import load_payment_method_rows


@api_login_required
@require_GET
def api_customers_search(request):
    q = request.GET.get("q", "")
    return json_ok({"results": search_customers(q)})


@api_login_required
@require_GET
def api_products_search(request):
    q = request.GET.get("q", "")
    return json_ok({"results": search_sale_products(q)})


@api_login_required
@require_GET
def api_suppliers_search(request):
    q = request.GET.get("q", "")
    return json_ok({"results": search_suppliers(q)})


@api_login_required
@require_GET
def api_categories_search(request):
    q = request.GET.get("q", "")
    return json_ok({"results": search_categories(q)})


@api_login_required
@require_GET
def api_units_search(request):
    q = request.GET.get("q", "")
    return json_ok({"results": search_units(q)})


@api_login_required
@require_GET
def api_accounts_search(request):
    q = request.GET.get("q", "")
    return json_ok({"results": search_accounts(q)})


@api_login_required
@require_GET
def api_payment_methods(request):
    return json_ok({"methods": load_payment_method_rows()})
