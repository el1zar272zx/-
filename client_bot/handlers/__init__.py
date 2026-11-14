from __future__ import annotations

from aiogram import Router

from ..middleware import RegistrationMiddleware
from .booking import router as booking_router
from .care import router as care_router
from .extras import router as extras_router
from .fallback import router as fallback_router
from .more import router as more_router
from .my_bookings import router as my_bookings_router
from .profile import router as profile_router
from .reviews import router as reviews_router
from .services import router as services_router
from .start import router as start_router


def setup_client_routers() -> Router:
    """Порядок важливий: /start і «Назад» першими, fallback — останнім."""
    router = Router(name="client_root")
    from shared.throttle import ThrottleMiddleware

    throttle = ThrottleMiddleware()
    router.message.outer_middleware(throttle)
    router.callback_query.outer_middleware(throttle)
    mw = RegistrationMiddleware()
    router.message.outer_middleware(mw)
    router.callback_query.outer_middleware(mw)
    router.include_router(start_router)
    router.include_router(services_router)
    router.include_router(booking_router)
    router.include_router(my_bookings_router)
    router.include_router(extras_router)
    router.include_router(more_router)
    router.include_router(care_router)
    router.include_router(profile_router)
    router.include_router(reviews_router)
    router.include_router(fallback_router)
    return router
