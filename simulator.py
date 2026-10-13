import random
import time

import requests

API_URL = "http://127.0.0.1:8000"

CORE_LAT = 13.0827
CORE_LONG = 80.2707


def trigger_crime_broadcast():
    payload = {
        "event_id": f"SIM-TN-{random.randint(10000, 99999)}",
        "status": "SIMULATED DEMO",
        "priority": "DEMO",
        "incident_type": "SIMULATED DEMO EVENT",
        "latitude": CORE_LAT,
        "longitude": CORE_LONG,
        "address": "Chennai, Tamil Nadu (simulated location)",
    }
    try:
        res = requests.post(f"{API_URL}/api/v1/ingest", json=payload, timeout=5)
        if res.status_code == 201:
            print("📡 [DEMO ONLY]: Synthetic Tamil Nadu test incident created.")
        elif res.status_code == 409:
            print("⚠️ [CRIME RADAR]: Incident event already exists; continuing patrol simulation.")
        else:
            res.raise_for_status()
    except requests.RequestException as exc:
        print(
            f"❌ Core API Transport Failure. Ensure your FastAPI server is online. Error details: {exc}"
        )


def run_responder_simulation():
    print("🚀 [DEMO ONLY]: Synthetic Chennai positions running. Press Ctrl+C to stop.")

    tactical_units = [
        {"unit_id": "SIM-TN-UNIT-101", "latitude": CORE_LAT + 0.016, "longitude": CORE_LONG + 0.016},
        {"unit_id": "SIM-TN-UNIT-102", "latitude": CORE_LAT - 0.014, "longitude": CORE_LONG - 0.015},
        {"unit_id": "SIM-TN-UNIT-103", "latitude": CORE_LAT + 0.019, "longitude": CORE_LONG - 0.011},
    ]

    while True:
        for unit in tactical_units:
            lat_step = (CORE_LAT - unit["latitude"]) * 0.09 + random.uniform(-0.0008, 0.0008)
            lon_step = (CORE_LONG - unit["longitude"]) * 0.09 + random.uniform(-0.0008, 0.0008)

            unit["latitude"] += lat_step
            unit["longitude"] += lon_step

            payload = {
                **unit,
                "status": "SIMULATED DEMO",
            }
            try:
                response = requests.post(
                    f"{API_URL}/api/v1/patrol-units",
                    json=payload,
                    timeout=5,
                )
                response.raise_for_status()
                print(
                    f"📡 [{unit['unit_id']}] position broadcast: "
                    f"({unit['latitude']:.5f}, {unit['longitude']:.5f})"
                )
            except requests.RequestException as exc:
                print(f"❌ Patrol telemetry broadcast failed for {unit['unit_id']}: {exc}")

        time.sleep(3)


if __name__ == "__main__":
    print("🎬 Initializing C.R.I.M.E. Radar Pipeline Simulation Subsystem...")
    trigger_crime_broadcast()
    run_responder_simulation()
