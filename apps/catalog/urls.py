from django.urls import path

from apps.catalog.views import (
    category_create,
    category_edit,
    category_list,
    component_info,
    product_card,
    product_create,
    product_edit,
    product_list,
    product_toggle_active,
    recipe_add,
    recipe_delete,
    recipe_list,
    unit_create,
    unit_edit,
    unit_list,
)

app_name = "catalog"

urlpatterns = [
    path("", product_list, name="product_list"),
    path("create/", product_create, name="product_create"),
    path("<int:pk>/edit/", product_edit, name="product_edit"),
    path("<int:pk>/toggle/", product_toggle_active, name="product_toggle"),
    path("<int:pk>/card/", product_card, name="product_card"),
    path("<int:pk>/recipe/", recipe_list, name="recipe_list"),
    path("<int:pk>/recipe/add/", recipe_add, name="recipe_add"),
    path("<int:pk>/recipe/<int:line_id>/delete/", recipe_delete, name="recipe_delete"),
    path("categories/", category_list, name="category_list"),
    path("categories/create/", category_create, name="category_create"),
    path("categories/<int:pk>/edit/", category_edit, name="category_edit"),
    path("units/", unit_list, name="unit_list"),
    path("units/create/", unit_create, name="unit_create"),
    path("units/<int:pk>/edit/", unit_edit, name="unit_edit"),
    path("component/<int:pk>/info/", component_info, name="component_info"),
]
