from django.urls import path

from apps.payroll import legacy_redirects

app_name = "payroll"

urlpatterns = [
    path("", legacy_redirects.employees, name="employees"),
    path("create/", legacy_redirects.employee_create, name="employee_create"),
    path("<int:pk>/", legacy_redirects.employee_detail, name="employee_detail"),
    path("<int:pk>/edit/", legacy_redirects.employee_edit, name="employee_edit"),
    path("<int:pk>/delete/", legacy_redirects.employee_delete, name="employee_delete"),
    path("<int:pk>/advance/", legacy_redirects.employee_advance, name="employee_advance"),
    path("<int:pk>/advance/<int:advance_id>/delete/", legacy_redirects.employee_advance_delete, name="employee_advance_delete"),
    path("<int:pk>/payout/", legacy_redirects.employee_payout, name="employee_payout"),
    path("<int:pk>/payout/<int:payout_id>/delete/", legacy_redirects.employee_payout_delete, name="employee_payout_delete"),
    path("<int:pk>/add-days/", legacy_redirects.employee_add_days, name="employee_add_days"),
    path("<int:pk>/add-hours/", legacy_redirects.employee_add_hours, name="employee_add_hours"),
    path("<int:pk>/cafe-purchase/", legacy_redirects.employee_cafe_purchase, name="employee_cafe_purchase"),
    path(
        "<int:pk>/cafe-purchase/<int:purchase_id>/delete/",
        legacy_redirects.employee_cafe_purchase_delete,
        name="employee_cafe_purchase_delete",
    ),
]
