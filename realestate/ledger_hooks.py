"""A small bridge so the real estate models can ask the accounting app a question
without importing it (accounting imports realestate, never the other way round).

The accounting app registers its answers when Django starts. If it is not
installed, every question gets its default answer and nothing changes.
"""

_hooks = {}


def register(name, func):
    _hooks[name] = func


def call(name, *args, default=None):
    func = _hooks.get(name)
    if func is None:
        return default
    return func(*args)
