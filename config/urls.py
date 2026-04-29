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
    path("app/", include("apps.core.shell_urls")),
    path("pos/", include("apps.pos.urls")),
    path("", include("apps.core.urls")),
]
