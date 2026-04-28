from django.urls import path

from apps.payroll import views

app_name = "payroll"

urlpatterns = [
    path("", views.employee_list, name="employees"),
    path("create/", views.employee_create, name="employee_create"),
    path("<int:pk>/", views.employee_detail, name="employee_detail"),
    path("<int:pk>/edit/", views.employee_edit, name="employee_edit"),
    path("<int:pk>/advance/", views.employee_advance_create, name="employee_advance"),
    path("<int:pk>/advance/<int:advance_id>/delete/", views.employee_advance_delete, name="employee_advance_delete"),
    path("<int:pk>/payout/", views.employee_payout_create, name="employee_payout"),
    path("<int:pk>/payout/<int:payout_id>/delete/", views.employee_payout_delete, name="employee_payout_delete"),
    path("<int:pk>/add-days/", views.employee_add_days, name="employee_add_days"),
    path("<int:pk>/add-hours/", views.employee_add_hours, name="employee_add_hours"),
    path("<int:pk>/cafe-purchase/", views.employee_cafe_purchase, name="employee_cafe_purchase"),
    path(
        "<int:pk>/cafe-purchase/<int:purchase_id>/delete/",
        views.employee_cafe_purchase_delete,
        name="employee_cafe_purchase_delete",
    ),
]
