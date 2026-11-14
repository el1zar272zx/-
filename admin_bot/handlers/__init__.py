from __future__ import annotations

from aiogram import Router

from .bookings import router as bookings_router
from .common import router as common_router
from .extras import router as extras_router
from .schedule import router as schedule_router
from .services import router as services_router
from .settings import router as settings_router
from .fallback import router as fallback_router
from .manual import router as manual_router
from .tools import router as tools_router
from .salon import router as salon_router
from .care import router as care_router
from .more import router as more_router
from .broadcast import router as broadcast_router
from .backup_ui import router as backup_router
from .notes import router as notes_router
from .stopwords import router as stopwords_router
from .works import router as works_router
from .pro import router as pro_router
from .studio import router as studio_router


def setup_admin_routers() -> Router:
    router = Router(name="admin_root")
    router.include_router(common_router)  # /start і «Меню» першими
    router.include_router(extras_router)  # кнопки нижнього меню мають пріоритет над введенням
    router.include_router(manual_router)  # після extras: нижні кнопки мають пріоритет
    router.include_router(tools_router)
    router.include_router(salon_router)
    router.include_router(care_router)
    router.include_router(more_router)
    router.include_router(broadcast_router)
    router.include_router(backup_router)
    router.include_router(notes_router)
    router.include_router(stopwords_router)
    router.include_router(works_router)
    router.include_router(pro_router)
    router.include_router(studio_router)
    router.include_router(bookings_router)
    router.include_router(schedule_router)
    router.include_router(services_router)
    router.include_router(settings_router)
    router.include_router(fallback_router)  # останнім: ловить усе незрозуміле
    return router
