from sqlalchemy import Column, Integer, String, DateTime, Time, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID as DbUUID
import uuid as PyUUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import datetime
from .db_base import Base
from .config import DEFAULT_TIMEZONE

STORE_ID_TYPE = DbUUID(as_uuid=True)

class Store(Base):
    __tablename__ = 'stores'
    id = Column(STORE_ID_TYPE, primary_key=True, default=PyUUID.uuid4, nullable=False)
    timezone_str = Column(String, nullable=False, default=DEFAULT_TIMEZONE)

    status_polls = relationship("StoreStatusPoll", back_populates="store", cascade="all, delete-orphan")
    business_hours = relationship("BusinessHour", back_populates="store", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Store(id={self.id}, timezone='{self.timezone_str}')>"

class StoreStatusPoll(Base):
    __tablename__ = 'store_status_polls'
    poll_id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(STORE_ID_TYPE, ForeignKey('stores.id'), nullable=False)
    timestamp_utc = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, nullable=False) 
    store = relationship("Store", back_populates="status_polls")
    __table_args__ = (
        Index('ix_store_status_polls_store_id_timestamp_utc', 'store_id', 'timestamp_utc'),
    )

    def __repr__(self):
        ts = self.timestamp_utc.isoformat() if self.timestamp_utc else 'None'
        return f"<StoreStatusPoll(store_id={self.store_id}, timestamp='{ts}', status='{self.status}')>"


class BusinessHour(Base):
    __tablename__ = 'business_hours'
    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(STORE_ID_TYPE, ForeignKey('stores.id'), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    start_time_local = Column(Time, nullable=False)
    end_time_local = Column(Time, nullable=False)
    store = relationship("Store", back_populates="business_hours")
    __table_args__ = (
        Index('ix_business_hours_store_id_day', 'store_id', 'day_of_week'),
    )

    def __repr__(self):
        start = self.start_time_local.isoformat() if self.start_time_local else 'None'
        end = self.end_time_local.isoformat() if self.end_time_local else 'None'
        return f"<BusinessHour(store_id={self.store_id}, day={self.day_of_week}, start='{start}', end='{end}')>"

class Report(Base):
    __tablename__ = 'reports'
    id = Column(String, primary_key=True) 
    status = Column(String, nullable=False, default='PENDING')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    report_file_path = Column(String, nullable=True)

    store_id = Column(STORE_ID_TYPE, ForeignKey('stores.id'), nullable=False, index=True)

    def __repr__(self):
        return f"<Report(id={self.id}, store_id={self.store_id}, status='{self.status}')>"