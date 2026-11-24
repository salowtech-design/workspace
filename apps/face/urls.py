"""
URL Configuration for Face Recognition System (FRS) APIs.
"""

from django.urls import path

from apps.face.views import (
    employee_face_status_view,
    enroll_face_from_device_view,
    face_system_status_view,
    issue_active_challenge_view,
    verify_live_face_view,
)

app_name = "face"

urlpatterns = [
    path("status/", face_system_status_view, name="status"),
    path("challenge/", issue_active_challenge_view, name="challenge"),
    path("verify/", verify_live_face_view, name="verify"),
    path("enroll/", enroll_face_from_device_view, name="enroll"),
    path("employee/<str:employee_id>/", employee_face_status_view, name="employee_status"),
]
