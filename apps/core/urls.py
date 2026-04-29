from django.urls import path

from apps.core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("session/open/", views.open_session_view, name="session_open"),
    path("session/close/", views.close_session_view, name="session_close"),
    path("session/summary/", views.session_summary, name="session_summary"),
]
