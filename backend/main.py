from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import inspect, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .database import Base, SessionLocal, engine, get_db
from .geography import TAMIL_NADU_FEATURE, is_in_tamil_nadu
from .models import CrimeIncident, PatrolUnit, PoliceStation, SourceSyncStatus


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

        def ensure_report_details_column(sync_connection):
            columns = {
                column["name"]
                for column in inspect(sync_connection).get_columns("crime_incidents")
            }
            if "report_details" not in columns:
                sync_connection.exec_driver_sql(
                    "ALTER TABLE crime_incidents "
                    "ADD COLUMN report_details VARCHAR(1000) NULL"
                )
            if "display_after" not in columns:
                sync_connection.exec_driver_sql(
                    "ALTER TABLE crime_incidents "
                    "ADD COLUMN display_after DATETIME NULL"
                )

        await connection.run_sync(ensure_report_details_column)

    async with SessionLocal() as db:
        incidents = await db.scalars(select(CrimeIncident))
        for incident in incidents:
            if not is_in_tamil_nadu(incident.latitude, incident.longitude):
                await db.delete(incident)
        patrol_units = await db.scalars(select(PatrolUnit))
        for unit in patrol_units:
            if not is_in_tamil_nadu(unit.latitude, unit.longitude):
                await db.delete(unit)
        stations = await db.scalars(select(PoliceStation))
        for station in stations:
            if not is_in_tamil_nadu(station.latitude, station.longitude):
                await db.delete(station)
        source_statuses = await db.scalars(select(SourceSyncStatus))
        for status in source_statuses:
            await db.delete(status)
        await db.commit()
    yield
    await engine.dispose()

app = FastAPI(
    title="C.R.I.M.E. Radar API Core",
    description="Tamil Nadu-scoped incident and patrol telemetry ingestion API.",
    lifespan=lifespan,
)


class IncidentCreate(BaseModel):
    event_id: str = Field(min_length=1, max_length=100)
    status: str = Field(min_length=1, max_length=40)
    priority: str = Field(min_length=1, max_length=40)
    incident_type: str = Field(min_length=1, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    address: str = Field(min_length=1, max_length=250)
    display_after: Optional[datetime] = None


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: str
    timestamp: datetime
    status: str
    priority: str
    incident_type: str
    latitude: float
    longitude: float
    address: str
    report_details: Optional[str] = None


class PatrolUnitTelemetry(BaseModel):
    unit_id: str = Field(min_length=1, max_length=100)
    status: str = Field(min_length=1, max_length=40)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class PatrolUnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    unit_id: str
    status: str
    latitude: float
    longitude: float
    updated_at: Optional[datetime] = None
    simulated: bool = False


class PoliceStationTelemetry(BaseModel):
    station_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    unit_id: str = Field(min_length=1, max_length=100)


class PoliceStationRead(PoliceStationTelemetry):
    model_config = ConfigDict(from_attributes=True)


@app.post("/api/v1/ingest", status_code=201, response_model=dict)
async def ingest_incident(
    incident: IncidentCreate,
    db: AsyncSession = Depends(get_db),
):
    if not is_in_tamil_nadu(incident.latitude, incident.longitude):
        raise HTTPException(
            status_code=422,
            detail="Incident coordinates are outside Tamil Nadu.",
        )
    existing = await db.scalar(
        select(CrimeIncident).where(CrimeIncident.event_id == incident.event_id)
    )
    if existing:
        raise HTTPException(status_code=409, detail="Incident event_id already exists.")

    db_incident = CrimeIncident(
        **incident.model_dump(),
        timestamp=datetime.utcnow(),
    )
    db.add(db_incident)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Incident event_id already exists.",
        ) from exc
    await db.refresh(db_incident)
    return {"status": "success", "vector_id": db_incident.id}


@app.get("/api/v1/active-incidents", response_model=List[IncidentRead])
async def get_active_incidents(db: AsyncSession = Depends(get_db)):
    result = await db.scalars(
        select(CrimeIncident)
        .where(CrimeIncident.status != "CLEARED")
        .where(
            or_(
                CrimeIncident.display_after.is_(None),
                CrimeIncident.display_after <= datetime.utcnow(),
            )
        )
        .order_by(CrimeIncident.timestamp.desc())
    )
    return [
        incident
        for incident in result.all()
        if is_in_tamil_nadu(incident.latitude, incident.longitude)
    ]


@app.get("/api/v1/sources")
async def get_data_sources():
    return [
        {
            "source_id": "tamil_nadu_agency_webhooks",
            "name": "Tamil Nadu-scoped agency telemetry",
            "url": "/docs",
            "jurisdiction": "Tamil Nadu, India",
            "boundary_source": "geoBoundaries India ADM1 · Tamil Nadu",
            "boundary_url": "https://www.geoboundaries.org/",
            "boundary_license": "CC BY 2.5 IN",
            "automated_public_feed": False,
            "last_success": None,
            "records_loaded": None,
            "data_through": None,
            "error": None,
            "disclaimer": (
                "Only reports inside Tamil Nadu are accepted. No Tamil Nadu Police "
                "CAD/911 or patrol GPS system is connected; live records require "
                "an authorized agency to send data to the documented webhook."
            ),
        }
    ]


@app.get("/api/v1/jurisdiction")
async def get_jurisdiction():
    return TAMIL_NADU_FEATURE


@app.post("/api/v1/police-stations", response_model=PoliceStationRead)
async def upsert_police_station(
    station: PoliceStationTelemetry,
    db: AsyncSession = Depends(get_db),
):
    if not is_in_tamil_nadu(station.latitude, station.longitude):
        raise HTTPException(
            status_code=422,
            detail="Station coordinates are outside Tamil Nadu.",
        )
    db_station = await db.get(PoliceStation, station.station_id)
    if db_station is None:
        db_station = PoliceStation(**station.model_dump())
        db.add(db_station)
    else:
        for field, value in station.model_dump(exclude={"station_id"}).items():
            setattr(db_station, field, value)
    await db.commit()
    await db.refresh(db_station)
    return db_station


@app.get("/api/v1/police-stations", response_model=List[PoliceStationRead])
async def get_police_stations(db: AsyncSession = Depends(get_db)):
    result = await db.scalars(select(PoliceStation).order_by(PoliceStation.name))
    return [
        station
        for station in result.all()
        if is_in_tamil_nadu(station.latitude, station.longitude)
    ]


@app.post("/api/v1/patrol-units", response_model=PatrolUnitRead)
async def ingest_patrol_unit(
    unit: PatrolUnitTelemetry,
    db: AsyncSession = Depends(get_db),
):
    if not is_in_tamil_nadu(unit.latitude, unit.longitude):
        raise HTTPException(
            status_code=422,
            detail="Patrol coordinates are outside Tamil Nadu.",
        )
    db_unit = await db.get(PatrolUnit, unit.unit_id)
    if db_unit is None:
        db_unit = PatrolUnit(unit_id=unit.unit_id, **unit.model_dump(exclude={"unit_id"}))
        db.add(db_unit)
    else:
        for field, value in unit.model_dump(exclude={"unit_id"}).items():
            setattr(db_unit, field, value)
        db_unit.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(db_unit)
    return db_unit


@app.get("/api/v1/patrol-units", response_model=List[PatrolUnitRead])
async def get_live_patrol_units(
    lat: Optional[float] = Query(default=None, ge=-90, le=90),
    lon: Optional[float] = Query(default=None, ge=-180, le=180),
    db: AsyncSession = Depends(get_db),
):
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail="Provide both lat and lon coordinates.")

    result = await db.scalars(
        select(PatrolUnit)
        .where(PatrolUnit.updated_at >= datetime.utcnow() - timedelta(minutes=2))
        .order_by(PatrolUnit.unit_id)
    )
    return [
        unit
        for unit in result.all()
        if is_in_tamil_nadu(unit.latitude, unit.longitude)
    ]
