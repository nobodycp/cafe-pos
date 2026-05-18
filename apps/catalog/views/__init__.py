"""Catalog views package — re-exports for shell_urls."""

from apps.catalog.views.search_api import category_quick_create
from apps.catalog.views.search_api import category_search
from apps.catalog.views.search_api import component_info
from apps.catalog.views.search_api import raw_materials_search
from apps.catalog.views.search_api import unit_quick_create
from apps.catalog.views.categories_units import category_create
from apps.catalog.views.categories_units import category_delete
from apps.catalog.views.categories_units import category_edit
from apps.catalog.views.categories_units import category_list
from apps.catalog.views.categories_units import unit_create
from apps.catalog.views.categories_units import unit_delete
from apps.catalog.views.categories_units import unit_edit
from apps.catalog.views.categories_units import unit_list
from apps.catalog.views.products import manufactured_product_create
from apps.catalog.views.products import product_card
from apps.catalog.views.products import product_create
from apps.catalog.views.products import product_delete
from apps.catalog.views.products import product_edit
from apps.catalog.views.products import product_list
from apps.catalog.views.products import product_manufacture_batch
from apps.catalog.views.products import product_manufacture_batch_void
from apps.catalog.views.products import product_toggle_active
from apps.catalog.views.products import product_workspace
from apps.catalog.views.products import recipe_add
from apps.catalog.views.products import recipe_delete
from apps.catalog.views.products import recipe_list
from apps.catalog.views.panels import _recipe_list_panel_context
from apps.catalog.views.panels import category_create_panel
from apps.catalog.views.panels import category_edit_panel
from apps.catalog.views.panels import manufactured_product_create_panel
from apps.catalog.views.panels import product_create_panel
from apps.catalog.views.panels import product_edit_panel
from apps.catalog.views.panels import product_manufacture_panel
from apps.catalog.views.panels import recipe_add_panel
from apps.catalog.views.panels import recipe_list_panel
from apps.catalog.views.panels import unit_create_panel
from apps.catalog.views.panels import unit_edit_panel
from apps.catalog.views._helpers import (
    _catalog_ctx,
    _catalog_redirect,
    _catalog_reverse,
    _recipe_form_rows,
    _save_recipe_lines_from_post,
    _unit_code,
)
