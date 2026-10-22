"""
Sample-data registry.

Every module writes a `seeders.py` containing functions decorated with @register(...).
The "Seed sample data" button (and `manage.py seed_data`) runs them all in `order`.
Seeders must be safe to run more than once (use get_or_create).
"""
from dataclasses import dataclass
from typing import Callable

from django.db import transaction


@dataclass
class Seeder:
    name: str
    func: Callable[[], str]
    order: int


_REGISTRY: list[Seeder] = []


def register(name: str, order: int = 100):
    def decorator(func):
        _REGISTRY.append(Seeder(name=name, func=func, order=order))
        return func

    return decorator


def all_seeders() -> list[Seeder]:
    return sorted(_REGISTRY, key=lambda s: (s.order, s.name))


def run_all() -> list[str]:
    """Run every seeder inside one transaction; if one fails nothing is saved."""
    results = []
    with transaction.atomic():
        for seeder in all_seeders():
            results.append(f"{seeder.name}: {seeder.func()}")
    return results
