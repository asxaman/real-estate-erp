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
