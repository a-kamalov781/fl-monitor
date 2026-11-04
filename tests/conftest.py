"""Роутер — модульный синглтон, а aiogram не даёт подключить роутер к двум Dispatcher."""
from __future__ import annotations

import pytest

from bot import handlers


@pytest.fixture(autouse=True)
def detach_router():
    yield
    handlers.router._parent_router = None
