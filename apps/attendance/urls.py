from django.urls import path

from apps.attendance.views import check_in_view, check_out_view, panel_view

app_name = "attendance"

urlpatterns = [
    path("", panel_view, name="panel"),
    path("check-in/", check_in_view, name="check_in"),
    path("check-out/", check_out_view, name="check_out"),
]
