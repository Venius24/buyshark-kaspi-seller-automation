"""
Общий стаб сессии БД для роутерных тестов, идущих через get_store_context.

Резолвер больше не принимает сессию через `Depends(get_db)` — он открывает и
закрывает свою, чтобы вернуть соединение в пул ДО того, как ручка уйдёт в
Kaspi на минуты (см. backend/security/store_context.py). Поэтому
`app.dependency_overrides[get_db]` до него больше не достаёт, и тестам нужна
подмена самой фабрики сессий.

Оверрайд `get_db` при этом остаётся нужен: его по-прежнему объявляют сами
ручки (например billing), и там он работает как раньше — этот хелпер его не
заменяет, а дополняет.
"""
from unittest.mock import patch


def install_store_context_db(test_case, fake_db):
    """Подменяет AsyncSessionLocal в store_context на сессию `fake_db`.

    Снимается автоматически через addCleanup — вызывающему не нужно ничего
    делать в tearDown.
    """
    class _SessionCM:
        async def __aenter__(self):
            return fake_db

        async def __aexit__(self, *exc_info):
            return False

    patcher = patch("backend.security.store_context.AsyncSessionLocal", lambda: _SessionCM())
    patcher.start()
    test_case.addCleanup(patcher.stop)
    return patcher
