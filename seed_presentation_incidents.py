import asyncio
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, select

from backend.database import Base, SessionLocal, engine
from backend.geography import is_in_tamil_nadu
from backend.models import CrimeIncident
from seed_training_data import INCIDENT_LOCATIONS


SHOWCASE_COUNT = 300
EVENT_ID_PREFIX = "DEMO-SHOWCASE-"
RANDOM_SEED = 20261016


def showcase_records():
    rng = random.Random(RANDOM_SEED)
    records = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for index in range(1, SHOWCASE_COUNT + 1):
        address, latitude, longitude = INCIDENT_LOCATIONS[
            (index - 1) % len(INCIDENT_LOCATIONS)
        ]
        for _ in range(100):
            candidate_latitude = latitude + rng.uniform(-0.003, 0.003)
            candidate_longitude = longitude + rng.uniform(-0.003, 0.003)
            if is_in_tamil_nadu(candidate_latitude, candidate_longitude):
                latitude, longitude = candidate_latitude, candidate_longitude
                break

        records.append(
            CrimeIncident(
                event_id=f"{EVENT_ID_PREFIX}{index:04d}",
                timestamp=now - timedelta(minutes=(index * 17) % (30 * 24 * 60)),
                status="SIMULATED DEMO",
                priority="DEMO",
                incident_type="SIMULATED DEMO EVENT",
                latitude=latitude,
                longitude=longitude,
                address=f"{address} (synthetic demo location)",
                report_details="Synthetic showcase record; not a real incident.",
            )
        )

    return records


async def seed_showcase_incidents():
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

            def ensure_report_columns(sync_connection):
                columns = {
                    column["name"]
                    for column in inspect(sync_connection).get_columns(
                        "crime_incidents"
                    )
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

            await connection.run_sync(ensure_report_columns)

        records = showcase_records()
        event_ids = [record.event_id for record in records]
        async with SessionLocal() as session:
            existing_ids = set(
                (
                    await session.scalars(
                        select(CrimeIncident.event_id).where(
                            CrimeIncident.event_id.in_(event_ids)
                        )
                    )
                ).all()
            )
            new_records = [
                record for record in records if record.event_id not in existing_ids
            ]
            session.add_all(new_records)
            await session.commit()

        print(
            f"Showcase incidents in MySQL: {SHOWCASE_COUNT} total "
            f"({len(new_records)} added, {len(existing_ids)} already existed). "
            "These records are marked SIMULATED DEMO and are not real incidents."
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_showcase_incidents())
