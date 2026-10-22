from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self):
        # Register this module's seed (and, in later modules, erase) steps.
        from . import seed  # noqa: F401

        # Audit trail: watch owners, properties, units and users.
        from django.apps import apps
        from django.db.models.signals import post_delete, post_save, pre_save

        from . import audit

        for label in audit.AUDITED_MODELS:
            model = apps.get_model(label)
            tag = f"audit-{label}"
            pre_save.connect(audit.remember_old, sender=model, dispatch_uid=f"{tag}-pre")
            post_save.connect(audit.log_save, sender=model, dispatch_uid=f"{tag}-save")
            post_delete.connect(audit.log_delete, sender=model, dispatch_uid=f"{tag}-del")

        # Unfold shows `user.avatar_url` in the sidebar's account block. Django's built-in User
        # has no such attribute, so add one that returns the profile picture (when there is one).
        from django.contrib.auth import get_user_model

        def avatar_url(user):
            profile = getattr(user, "profile", None)
            if profile is not None and profile.photo:
                return profile.photo.url
            raise AttributeError("no profile picture")  # Unfold then shows the initial letter

        User = get_user_model()
        if not hasattr(User, "avatar_url"):
            User.add_to_class("avatar_url", property(avatar_url))
