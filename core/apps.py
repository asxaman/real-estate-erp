from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self):
        # Register this module's seed (and, in later modules, erase) steps.
        from . import seed  # noqa: F401

        # Date and time format chosen on the System settings page.
        from . import formats

        formats.install()

        # The small menu next to a dropdown has no "Delete selected ..." entry.
        from django.contrib.admin.widgets import RelatedFieldWidgetWrapper

        original_init = RelatedFieldWidgetWrapper.__init__

        def init_without_delete(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self.can_delete_related = False

        RelatedFieldWidgetWrapper.__init__ = init_without_delete

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

        # Older log entries get their personal numbers hidden after `migrate`.
        post_migrate.connect(audit.scrub_old_entries, sender=self, dispatch_uid="audit-scrub")

        # Replaced or removed uploads are deleted from the disk.
        from . import files

        files.connect()

        # Lock out password guessing on the login page.
        from django.contrib.auth.signals import user_logged_in, user_login_failed

        from . import lockout

        user_login_failed.connect(lockout.record_failure, dispatch_uid="lockout-failed")
        user_logged_in.connect(lockout.clear_failures, dispatch_uid="lockout-ok")

        from . import audit as audit_log

        user_logged_in.connect(audit_log.log_login, dispatch_uid="audit-login")

        # Unfold shows `user.avatar_url` in the sidebar's account block. Django's built-in User
        # has no such attribute, so add one that returns the profile picture (when there is one).
        from django.contrib.auth import get_user_model

        def avatar_url(user):
            profile = getattr(user, "profile", None)
            if profile is not None and profile.photo:
                return profile.photo.url
            return ""  # empty: Unfold then shows the person's initial letter instead

        User = get_user_model()
        if not hasattr(User, "avatar_url"):
            User.add_to_class("avatar_url", property(avatar_url))
