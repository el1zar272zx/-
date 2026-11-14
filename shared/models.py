# shared/models.py
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class City(Base):
    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Client(Base):
    """Клієнт, що поділився контактом: імʼя й номер зберігаємо один раз."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)  # нотатка адміна про клієнта
    birthday: Mapped[dt.date | None] = mapped_column(Date)  # рік може бути умовним
    lang: Mapped[str] = mapped_column(String(2), default="uk", nullable=False)
    intake: Mapped[str | None] = mapped_column(Text)  # анкета протипоказань
    last_reengage_on: Mapped[dt.date | None] = mapped_column(Date)
    birthday_greeted_year: Mapped[int | None] = mapped_column(Integer)
    contact_pref: Mapped[str] = mapped_column(String(8), default="tg", nullable=False)
    pref_days: Mapped[str | None] = mapped_column(String(20))  # напр. "0,2,4" (пн,ср,пт)
    booking_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    photo_consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # згода на фото до/після
    quiet: Mapped[str | None] = mapped_column(String(8))  # тихі години, напр. "21-9"
    about: Mapped[str | None] = mapped_column(Text)  # «про мене» від клієнта: тип шкіри, що болить
    tags: Mapped[str | None] = mapped_column(Text)  # мітки майстра, через кому: «VIP, алергік»
    simple: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # простий режим меню
    tips_off: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # не слати поради тижня


class ServiceCategory(Base):
    __tablename__ = "service_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    prices_editable_in_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("service_categories.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    prep: Mapped[str | None] = mapped_column(Text)  # як підготуватися до процедури
    recall_days: Mapped[int | None] = mapped_column(Integer)  # через скільки днів радити повтор
    followup_days: Mapped[int | None] = mapped_column(Integer)  # через скільки днів спитати про самопочуття
    sale_price: Mapped[int | None] = mapped_column(Integer)  # акційна ціна
    sale_until: Mapped[dt.date | None] = mapped_column(Date)  # акція діє до цієї дати включно
    photo: Mapped[bytes | None] = mapped_column(LargeBinary)  # фото послуги для клієнтів
    contra: Mapped[str | None] = mapped_column(Text)  # протипоказання, по одному в рядку
    consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # потрібна письмова згода
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    category: Mapped["ServiceCategory"] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("category_id", "name", name="uq_service_category_name"),
        CheckConstraint("duration_minutes > 0", name="ck_service_duration_positive"),
    )


class PaymentMethod(enum.Enum):
    CASH = "CASH"
    CARD = "CARD"


class BookingStatus(enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    NO_SHOW = "NO_SHOW"


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    city_id: Mapped[int] = mapped_column(
        ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False
    )
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )

    client_telegram_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    client_name: Mapped[str] = mapped_column(String(150), nullable=False)
    client_phone: Mapped[str] = mapped_column(String(30), nullable=False)

    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    start_time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[dt.time] = mapped_column(Time, nullable=False)

    # Ціна на момент запису (щоб зміна прайсу не "попливла" в старих записах)
    price: Mapped[int | None] = mapped_column(Integer)

    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="payment_method_enum", native_enum=False, length=20),
        nullable=False,
    )

    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, name="booking_status_enum", native_enum=False, length=20),
        default=BookingStatus.PENDING,
        nullable=False,
        index=True,
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    wish: Mapped[str | None] = mapped_column(Text)  # побажання клієнта майстру
    visit_note: Mapped[str | None] = mapped_column(Text)  # нотатка майстра після візиту
    note_done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # нотатку написано або пропущено
    flags: Mapped[str | None] = mapped_column(Text)  # «червоні прапорці» з анкети й протипоказань

    # Повна назва, якщо в записі кілька послуг ("Масаж спини + Антицелюліт")
    service_title: Mapped[str | None] = mapped_column(String(400))
    recall_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    followup_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    is_notified_24h: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    aftercare_scheduled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    client_confirmed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_notified_12h: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_notified_2h: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_admin_notified_soon: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_review_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    city: Mapped["City"] = relationship(lazy="selectin")
    service: Mapped["Service"] = relationship(lazy="selectin")

    __table_args__ = (Index("ix_booking_date_time", "date", "start_time", "end_time"),)

    @property
    def title(self) -> str:
        """Назва для показу: усі послуги запису або одна."""
        return self.service_title or self.service.name


class Review(Base):
    """Оцінка клієнта після візиту (1–5) і необовʼязковий коментар."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    client_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    booking: Mapped["Booking"] = relationship(lazy="selectin")

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_review_rating"),
    )


class WaitlistEntry(Base):
    """Клієнт чекає, поки на дату звільниться місце потрібної тривалості."""

    __tablename__ = "waitlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    city_id: Mapped[int] = mapped_column(
        ForeignKey("cities.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("client_telegram_id", "city_id", "date", name="uq_waitlist"),
    )


class Schedule(Base):
    """
    Тижневий шаблон графіка для міста (по днях тижня).
    Для вихідного дня is_day_off=True; години при цьому зберігаються (але ігноруються),
    щоб при поверненні дня в роботу не вводити їх заново.
    """

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[int] = mapped_column(
        ForeignKey("cities.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Пн .. 6=Нд
    start_time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    is_day_off: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("city_id", "weekday", name="uq_schedule_city_weekday"),
        CheckConstraint("start_time < end_time", name="ck_schedule_time_order"),
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_schedule_weekday"),
    )


class ScheduleException(Base):
    """Виняток з тижневого графіка на конкретну дату (вихідний або інші години)."""

    __tablename__ = "schedule_exceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[int] = mapped_column(
        ForeignKey("cities.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    is_day_off: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    start_time: Mapped[dt.time | None] = mapped_column(Time)
    end_time: Mapped[dt.time | None] = mapped_column(Time)

    __table_args__ = (
        UniqueConstraint("city_id", "date", name="uq_schedule_exception_city_date"),
        CheckConstraint(
            "is_day_off OR (start_time IS NOT NULL AND end_time IS NOT NULL "
            "AND start_time < end_time)",
            name="ck_schedule_exception_hours",
        ),
    )


class BlockedSlot(Base):
    """Заблокований проміжок часу (перерва, справи) — діє на обидва міста."""

    __tablename__ = "blocked_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        CheckConstraint("start_time < end_time", name="ck_blocked_slot_order"),
    )


class CardSettings(Base):
    __tablename__ = "card_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_number: Mapped[str | None] = mapped_column(String(64))
    card_holder: Mapped[str | None] = mapped_column(String(128))
    bank_name: Mapped[str | None] = mapped_column(String(128))
    comment: Mapped[str | None] = mapped_column(Text)

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SalonInfo(Base):
    """Налаштування «Про салон»: адреса, телефон, посилання на карту (ключ-значення)."""

    __tablename__ = "salon_info"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")


class SalonPhoto(Base):
    """Фото салону. Зберігаємо самі байти, бо file_id діє лише в межах одного бота."""

    __tablename__ = "salon_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    caption: Mapped[str | None] = mapped_column(String(200))


class WorkPhoto(Base):
    """Фото робіт майстра для послуги: клієнти бачать їх у каталозі («Приклади робіт»)."""

    __tablename__ = "work_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    caption: Mapped[str | None] = mapped_column(String(200))


class FaqItem(Base):
    __tablename__ = "faq_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(String(200), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)


class AftercareRule(Base):
    """Обмеження після процедури: що не можна і скільки годин."""

    __tablename__ = "aftercare_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(String(200), nullable=False)
    hours: Mapped[int] = mapped_column(Integer, nullable=False)


class AftercareNotice(Base):
    """Заплановане повідомлення клієнту: 'start' (перелік обмежень) або 'free' (вже можна)."""

    __tablename__ = "aftercare_notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    due_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ClientPhoto(Base):
    """Фото до/після (бачить лише майстер, лише за згодою клієнта)."""

    __tablename__ = "client_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), default="other", nullable=False)  # before|after|other
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ScheduledBroadcast(Base):
    """Відкладена розсилка: текст, аудиторія й час відправки (за київським часом)."""

    __tablename__ = "scheduled_broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    segment: Mapped[str] = mapped_column(String(32), nullable=False, default="all")
    run_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, index=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# ---------- Нові таблиці (оновлення «клієнти й мама») ----------


class Favorite(Base):
    """Обрана клієнтом послуга."""

    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (UniqueConstraint("client_telegram_id", "service_id", name="uq_favorite"),)


class Suggestion(Base):
    """Ідея послуги від клієнта; інші клієнти можуть її підтримати."""

    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    client_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SuggestionVote(Base):
    __tablename__ = "suggestion_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    suggestion_id: Mapped[int] = mapped_column(
        ForeignKey("suggestions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (UniqueConstraint("suggestion_id", "client_telegram_id", name="uq_sugg_vote"),)


class AdminTask(Base):
    """Особисті завдання майстра («купити олію», «зателефонувати Ані»)."""

    __tablename__ = "admin_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QuickReply(Base):
    """Шаблон швидкої відповіді клієнту."""

    __tablename__ = "quick_replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(500), nullable=False)


class ConsentRecord(Base):
    """Згода клієнта на процедуру (дата, текст, послуга)."""

    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    service_id: Mapped[int] = mapped_column(Integer, nullable=False)
    booking_id: Mapped[int | None] = mapped_column(Integer, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BookingDraft(Base):
    """Незавершений запис: щоб нагадати клієнту й дати продовжити."""

    __tablename__ = "booking_drafts"

    client_telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    service_ids: Mapped[str] = mapped_column(String(100), nullable=False)
    city_id: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)  # місцевий час
    nudged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ClientQuestion(Base):
    """Запитання клієнтів майстру: з них складаємо «топ питань»."""

    __tablename__ = "client_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ActionLog(Base):
    """Журнал дій адмінів (бачить лише власник)."""

    __tablename__ = "action_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    actor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    details: Mapped[str | None] = mapped_column(Text)


class LibraryItem(Base):
    """Довідник «Корисне»: поради тижня (tip), вправи (ex) і словничок термінів (term)."""

    __tablename__ = "library_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ClientData(Base):
    """Особисті дані клієнта «ключ-значення»: паспорт шкіри, вподобання сеансу, челенджі, курси, прапорці."""

    __tablename__ = "client_data"
    __table_args__ = (UniqueConstraint("client_telegram_id", "key", name="uq_client_data"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WellbeingEntry(Base):
    """Щоденник самопочуття: рівень болю/напруги 1–10 (до візиту, після візиту чи просто зараз)."""

    __tablename__ = "wellbeing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    booking_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8), default="now", nullable=False)  # before|after|now
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatMessage(Base):
    """Листування клієнта з майстром (питання, голосові, відповіді) для історії в боті."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)  # in = від клієнта, out = від майстра
    kind: Mapped[str] = mapped_column(String(8), default="text", nullable=False)  # text|voice|lost
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class Playlist(Base):
    """Плейлисти для сеансів (бачить лише майстер)."""

    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(String(400), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))
