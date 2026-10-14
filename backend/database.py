import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sqlalchemy.orm import declarative_base

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DATABASE_URL = os.getenv("CRIME_RADAR_DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "CRIME_RADAR_DATABASE_URL is required. Configure MySQL with "
        "configure_mysql.py before starting the API."
    )
if not DATABASE_URL.startswith("mysql+asyncmy://"):
    raise RuntimeError(
        "CRIME_RADAR_DATABASE_URL must use the mysql+asyncmy:// driver."
    )

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

Base = declarative_base()


async def get_db():
    async with SessionLocal() as db:
        yield db
