from django.contrib import admin

from apps.posts.models import Post, PostComment, PostTemplate


@admin.register(PostTemplate)
class PostTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "is_active", "created_at")
    list_filter = ("category", "is_active")
    search_fields = ("name", "template_text")


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "team", "category", "status", "work_date", "created_at")
    list_filter = ("status", "category", "work_date", "team")
    search_fields = ("title", "content", "author__employee_id", "author__full_name")


@admin.register(PostComment)
class PostCommentAdmin(admin.ModelAdmin):
    list_display = ("post", "author", "created_at")
    search_fields = ("content", "author__employee_id", "author__full_name")
