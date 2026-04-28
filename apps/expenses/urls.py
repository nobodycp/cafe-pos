from django.urls import path

from apps.expenses import views

app_name = "expenses"

urlpatterns = [
    path("", views.expense_list, name="list"),
    path("<int:pk>/edit/", views.expense_edit, name="edit"),
    path("<int:pk>/delete/", views.expense_delete, name="delete"),
    path("create/", views.expense_create, name="create"),
    path("categories/", views.expense_category_list, name="categories"),
    path("categories/create/", views.expense_category_create, name="category_create"),
    path("categories/<int:pk>/edit/", views.expense_category_edit, name="category_edit"),
]
