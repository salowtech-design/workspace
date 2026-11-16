from django.urls import path

from apps.posts import views

app_name = "posts"

urlpatterns = [
    path("", views.post_feed_view, name="feed"),
    path("create/", views.post_create_view, name="create"),
    path("<int:post_id>/", views.post_detail_view, name="detail"),
    path("<int:post_id>/edit/", views.post_edit_view, name="edit"),
    path("<int:post_id>/review/", views.post_review_view, name="review"),
    path("<int:post_id>/comments/", views.post_comment_view, name="comment"),
]
