from sqlalchemy import String, Double, Integer, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class SpotCheck(Base):
    """A frozen spot-check roster drawn from in-service assets of one dataset."""

    __tablename__ = 'spot_checks'
    __table_args__ = (
        UniqueConstraint('created_by', 'request_no', name='uq_spot_check_request'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    model: Mapped[str | None] = mapped_column(String(40), nullable=True)
    size: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='open')
    request_no: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    closed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    cancelled_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    result_dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'), nullable=True)
    close_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    roster_checksum: Mapped[str] = mapped_column(String(64))


class SpotCheckItem(Base):
    """One frozen asset in a roster, including the mileage at draw time."""

    __tablename__ = 'spot_check_items'
    __table_args__ = (
        UniqueConstraint('check_id', 'asset', name='uq_spot_item_asset'),
        Index('ix_spot_item_check', 'check_id'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey('spot_checks.id'))
    asset: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(40))
    original_km: Mapped[float] = mapped_column(Double)
    position: Mapped[int] = mapped_column(Integer)
    reported_km: Mapped[float | None] = mapped_column(Double, nullable=True)
    reported_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    report_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reported_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class SpotReservation(Base):
    """One row per asset currently held by an unfinished spot check.

    The primary key on asset is the cross-batch exclusivity guarantee. Rows are
    deleted atomically when a check closes or is cancelled.
    """

    __tablename__ = 'spot_reservations'

    asset: Mapped[str] = mapped_column(String(80), primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey('spot_checks.id'))
    created_at: Mapped[float] = mapped_column(Double)


class SpotReport(Base):
    """An accepted per-item report, keyed by the caller supplied report number."""

    __tablename__ = 'spot_reports'
    __table_args__ = (
        UniqueConstraint('check_id', 'report_no', name='uq_spot_report_no'),
        UniqueConstraint('check_id', 'asset', name='uq_spot_report_asset'),
        Index('ix_spot_report_check', 'check_id'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey('spot_checks.id'))
    report_no: Mapped[str] = mapped_column(String(64))
    asset: Mapped[str] = mapped_column(String(80))
    km: Mapped[float] = mapped_column(Double)
    failed: Mapped[int] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[float] = mapped_column(Double)
