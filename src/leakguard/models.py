"""SQLAlchemy tables for the canonical data model (SPEC section 5).

Money is integer cents plus a currency code. Dates are ISO dates.
Datetimes are stored in UTC. Every table carries portfolio_id.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    owner_name: Mapped[str] = mapped_column(String(200))
    owner_type: Mapped[str] = mapped_column(String(32))


class ManagementCompany(Base):
    __tablename__ = "management_companies"
    __table_args__ = (UniqueConstraint("portfolio_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    name: Mapped[str] = mapped_column(String(200))


class Property(Base):
    __tablename__ = "properties"
    __table_args__ = (UniqueConstraint("portfolio_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    management_company_id: Mapped[int] = mapped_column(ForeignKey("management_companies.id"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(100))
    segment: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(32))
    room_count: Mapped[int | None] = mapped_column(Integer)
    pms_name: Mapped[str | None] = mapped_column(String(100))
    timezone: Mapped[str | None] = mapped_column(String(64))

    management_company: Mapped[ManagementCompany] = relationship()


class SourceFile(Base):
    __tablename__ = "source_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    source_name: Mapped[str] = mapped_column(String(64))
    mapping_name: Mapped[str | None] = mapped_column(String(100))
    file_name: Mapped[str] = mapped_column(String(300))
    relative_path: Mapped[str] = mapped_column(String(600))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="imported")  # imported/skipped/failed
    message: Mapped[str | None] = mapped_column(Text)
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    date_min: Mapped[date | None] = mapped_column(Date)
    date_max: Mapped[date | None] = mapped_column(Date)
    imported_at: Mapped[datetime] = mapped_column(DateTime)
    data_mode: Mapped[str] = mapped_column(String(8))


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        Index("ix_res_prop_ota", "property_id", "ota_confirmation_no"),
        Index("ix_res_prop_pms", "property_id", "pms_confirmation_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    pms_confirmation_no: Mapped[str] = mapped_column(String(64))
    ota_confirmation_no: Mapped[str | None] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(16))
    payment_model: Mapped[str] = mapped_column(String(16))
    guest_last_name: Mapped[str | None] = mapped_column(String(100))
    arrival_date: Mapped[date] = mapped_column(Date)
    departure_date: Mapped[date] = mapped_column(Date)
    nights: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    room_revenue_cents: Mapped[int | None] = mapped_column(Integer)
    tax_cents: Mapped[int | None] = mapped_column(Integer)
    folio_total_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"))


class PmsPayment(Base):
    __tablename__ = "pms_payments"
    __table_args__ = (Index("ix_pay_prop_pms", "property_id", "pms_confirmation_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    pms_confirmation_no: Mapped[str] = mapped_column(String(64))
    posting_date: Mapped[date] = mapped_column(Date)
    payment_type: Mapped[str | None] = mapped_column(String(100))
    card_last4: Mapped[str | None] = mapped_column(String(4))
    amount_cents: Mapped[int] = mapped_column(Integer)  # reversals are negative
    currency: Mapped[str] = mapped_column(String(3))
    is_reversal: Mapped[bool] = mapped_column(Boolean, default=False)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"))


class VccRecord(Base):
    __tablename__ = "vcc_records"
    __table_args__ = (Index("ix_vcc_prop_ota", "property_id", "ota_confirmation_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    ota: Mapped[str] = mapped_column(String(16))
    ota_confirmation_no: Mapped[str] = mapped_column(String(64))
    card_last4: Mapped[str | None] = mapped_column(String(4))
    vcc_amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    activation_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    ota_status: Mapped[str | None] = mapped_column(String(64))
    # Not in SPEC section 5. OTA card reports usually carry these, and the
    # fuzzy fallback (SPEC section 7) cannot run without them.
    guest_last_name: Mapped[str | None] = mapped_column(String(100))
    arrival_date: Mapped[date | None] = mapped_column(Date)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"))


class ProcessorTransaction(Base):
    __tablename__ = "processor_transactions"
    __table_args__ = (Index("ix_proc_prop_last4", "property_id", "card_last4"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    transaction_date: Mapped[date] = mapped_column(Date)
    settlement_date: Mapped[date | None] = mapped_column(Date)
    card_last4: Mapped[str | None] = mapped_column(String(4))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    transaction_type: Mapped[str] = mapped_column(String(16))
    auth_code: Mapped[str | None] = mapped_column(String(32))
    reference: Mapped[str | None] = mapped_column(String(64))
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"))


class Exception_(Base):
    """One row per VCC. Rows where is_exception is False (CHARGED_OK,
    NOT_YET_DUE) are kept for coverage and volume stats."""

    __tablename__ = "exceptions"
    __table_args__ = (UniqueConstraint("property_id", "vcc_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    vcc_key: Mapped[str] = mapped_column(String(160))  # ota|confirmation|last4
    vcc_record_id: Mapped[int | None] = mapped_column(Integer)
    reservation_id: Mapped[int | None] = mapped_column(Integer)
    ota: Mapped[str] = mapped_column(String(16))
    ota_confirmation_no: Mapped[str] = mapped_column(String(64))
    pms_confirmation_no: Mapped[str | None] = mapped_column(String(64))
    card_last4: Mapped[str | None] = mapped_column(String(4))
    guest_last_name: Mapped[str | None] = mapped_column(String(100))
    exception_type: Mapped[str] = mapped_column(String(32), index=True)
    is_exception: Mapped[bool] = mapped_column(Boolean, default=True)
    match_method: Mapped[str] = mapped_column(String(8))  # exact / fuzzy / none
    match_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[str] = mapped_column(String(8), default="high")
    rule: Mapped[str] = mapped_column(Text, default="")
    amount_at_risk_cents: Mapped[int] = mapped_column(Integer, default=0)
    expected_cents: Mapped[int] = mapped_column(Integer, default=0)
    charged_cents: Mapped[int] = mapped_column(Integer, default=0)
    settled_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    arrival_date: Mapped[date | None] = mapped_column(Date)
    departure_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    days_to_expiry: Mapped[int | None] = mapped_column(Integer)
    priority_score: Mapped[float] = mapped_column(default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    assigned_to: Mapped[str | None] = mapped_column(String(100))
    recovered_cents: Mapped[int] = mapped_column(Integer, default=0)
    recovered_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)

    events: Mapped[list[ExceptionEvent]] = relationship(
        back_populates="exception", order_by="ExceptionEvent.timestamp"
    )


class ExceptionEvent(Base):
    __tablename__ = "exception_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    exception_id: Mapped[int] = mapped_column(ForeignKey("exceptions.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    old_value: Mapped[str | None] = mapped_column(String(200))
    new_value: Mapped[str | None] = mapped_column(String(200))
    user: Mapped[str] = mapped_column(String(100))
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text)

    exception: Mapped[Exception_] = relationship(back_populates="events")


class ImportIssue(Base):
    __tablename__ = "import_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    source_file_id: Mapped[int] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), index=True
    )
    row_number: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(8))  # error / warning
    field: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)


DATA_TABLES = (Reservation, PmsPayment, VccRecord, ProcessorTransaction)
