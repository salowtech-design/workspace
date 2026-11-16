from django.urls import path

from apps.certificates import views

app_name = "certificates"

urlpatterns = [
    path("", views.certificate_list_view, name="list"),
    path("issue/", views.certificate_issue_view, name="issue"),
    path("<uuid:cert_uuid>/", views.certificate_view, name="view"),
    path("<uuid:cert_uuid>/svg/", views.certificate_svg_view, name="svg"),
    path("<uuid:cert_uuid>/revoke/", views.certificate_revoke_view, name="revoke"),
]
