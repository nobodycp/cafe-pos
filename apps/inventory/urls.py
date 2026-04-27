from django.urls import path

from apps.inventory import views

app_name = "inventory"

urlpatterns = [
    path("", views.inventory_home, name="home"),
    path("movements/", views.movement_list, name="movements"),
    path("adjust/", views.stock_adjust, name="adjust"),
    path("raw-materials/", views.raw_material_list, name="raw_materials"),
    path("raw-materials/create/", views.raw_material_create, name="raw_material_create"),
    path("raw-materials/<int:pk>/edit/", views.raw_material_edit, name="raw_material_edit"),
    path("raw-materials/<int:pk>/card/", views.raw_material_card, name="raw_material_card"),
    path("alerts/", views.low_stock_alerts, name="low_stock_alerts"),
    path("stocktake/", views.stocktake_list, name="stocktake_list"),
    path("stocktake/create/", views.stocktake_create, name="stocktake_create"),
    path("stocktake/<int:pk>/", views.stocktake_detail, name="stocktake_detail"),
    path("stocktake/<int:pk>/edit/", views.stocktake_edit, name="stocktake_edit"),
    path("stocktake/<int:pk>/approve/", views.stocktake_approve, name="stocktake_approve"),
]
