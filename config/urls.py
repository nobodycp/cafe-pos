from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = "إدارة المقهى — POS"
admin.site.site_title = "POS"

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path(
        "accounts/logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),
    path("i18n/", include("django.conf.urls.i18n")),
    path("pos/", include("apps.pos.urls")),
    path("products/", include("apps.catalog.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("purchasing/", include("apps.purchasing.urls")),
    path("customers/", include("apps.contacts.urls")),
    path("payroll/", include("apps.payroll.urls")),
    path("expenses/", include("apps.expenses.urls")),
    path("reports/", include("apps.reports.urls")),
    path("accounting/", include("apps.accounting.urls")),
    path("billing/", include("apps.billing.urls")),
    path("", include("apps.core.urls")),
]
