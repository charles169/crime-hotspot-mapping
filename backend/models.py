from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String

from .database import Base


class CrimeIncident(Base):
    __tablename__ = "crime_incidents"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(100), unique=True, index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    status = Column(String(40), default="ACTIVE_DISPATCH")
    priority = Column(String(40), nullable=False)
    incident_type = Column(String(120), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    address = Column(String(250), nullable=False)
    report_details = Column(String(1000), nullable=True)
    display_after = Column(DateTime, nullable=True)


class PatrolUnit(Base):
    __tablename__ = "patrol_units"

    unit_id = Column(String(100), primary_key=True, index=True)
    status = Column(String(40), nullable=False, default="AVAILABLE")
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PoliceStation(Base):
    __tablename__ = "police_stations"

    station_id = Column(String(100), primary_key=True)
    name = Column(String(120), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    unit_id = Column(String(100), nullable=False)


class SourceSyncStatus(Base):
    __tablename__ = "source_sync_status"

    source_id = Column(String(100), primary_key=True)
    last_attempt = Column(DateTime, nullable=True)
    last_success = Column(DateTime, nullable=True)
    records_loaded = Column(Integer, nullable=False, default=0)
    data_through = Column(DateTime, nullable=True)
    error = Column(String(1000), nullable=True)
