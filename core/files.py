"""
Delete an uploaded file from the disk when it is replaced or removed on its record.

Without this, replacing a tenant's passport scan would leave the old scan behind in the media folder
(personal data that nothing points to any more). Deleting a whole record is handled elsewhere.
"""
from functools import partial

from django.db import models, transaction

APPS = ("core", "properties", "tenants")


def _file_fields(model):
    return [f for f in model._meta.concrete_fields if isinstance(f, models.FileField)]


def remember_files(sender, instance, raw=False, **kwargs):
    instance._old_file_names = {}
    if raw or instance.pk is None or instance._state.adding:
        return
    fields = _file_fields(sender)
    if not fields:
        return
    names = [f.name for f in fields]
    instance._old_file_names = sender._default_manager.filter(pk=instance.pk).values(*names).first() or {}


def delete_replaced_files(sender, instance, raw=False, **kwargs):
    old = getattr(instance, "_old_file_names", None)
    if raw or not old:
        return
    for field in _file_fields(sender):
        previous = old.get(field.name) or ""
        current = getattr(instance, field.name)
        current_name = getattr(current, "name", "") or ""
        if previous and previous != current_name:
            # only after the whole save has been committed, so a failed save keeps the old file
            transaction.on_commit(partial(field.storage.delete, previous))
    instance._old_file_names = {}


def connect():
    from django.apps import apps
    from django.db.models.signals import post_save, pre_save

    for model in apps.get_models():
        if model._meta.app_label in APPS and _file_fields(model):
            tag = f"files-{model._meta.label}"
            pre_save.connect(remember_files, sender=model, dispatch_uid=f"{tag}-pre")
            post_save.connect(delete_replaced_files, sender=model, dispatch_uid=f"{tag}-post")
