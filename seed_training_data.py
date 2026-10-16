import os

import requests


API_URL = os.getenv("CRIME_RADAR_API_URL", "http://127.0.0.1:8000").rstrip("/")

INCIDENT_LOCATIONS = [
    ("T. Nagar, Chennai", 13.0418, 80.2337),
    ("Egmore, Chennai", 13.0604, 80.2496),
    ("Mylapore, Chennai", 13.0337, 80.2702),
    ("Anna Nagar, Chennai", 13.0850, 80.2101),
    ("Guindy, Chennai", 13.0108, 80.2150),
    ("Perambur, Chennai", 13.1143, 80.2326),
    ("Marina Beach, Chennai", 13.0475, 80.2824),
    ("Little Mount, Chennai", 13.0067, 80.2206),
    ("RS Puram, Coimbatore", 11.0050, 76.9520),
    ("Gandhipuram, Coimbatore", 11.0170, 76.9670),
    ("Saibaba Colony, Coimbatore", 11.0250, 76.9400),
    ("Anna Nagar, Madurai", 9.9430, 78.1450),
    ("Tallakulam, Madurai", 9.9330, 78.1300),
    ("Teppakulam, Madurai", 9.9100, 78.1450),
    ("Cantonment, Tiruchirappalli", 10.8050, 78.6850),
    ("Srirangam, Tiruchirappalli", 10.8620, 78.6950),
    ("Thillai Nagar, Tiruchirappalli", 10.8180, 78.6800),
    ("Fairlands, Salem", 11.6700, 78.1550),
    ("Hasthampatti, Salem", 11.6800, 78.1650),
    ("Palayamkottai, Tirunelveli", 8.7250, 77.7100),
    ("Tirunelveli Junction", 8.7350, 77.7000),
    ("Perundurai, Erode", 11.2760, 77.5840),
    ("Erode Junction", 11.3420, 77.7280),
    ("Katpadi, Vellore", 12.9690, 79.1370),
    ("Vellore Fort", 12.9160, 79.1330),
    ("Avinashi Road, Tiruppur", 11.1110, 77.3390),
    ("Tiruppur Junction", 11.1080, 77.3480),
    ("Thanjavur Medical College Road", 10.7760, 79.1370),
    ("Thanjavur Junction", 10.7850, 79.1390),
    ("Cuddalore OT", 11.7460, 79.7640),
    ("Thirupapuliyur, Cuddalore", 11.7380, 79.7500),
    ("Beach Road, Thoothukudi", 8.7650, 78.1350),
    ("Thoothukudi Junction", 8.7650, 78.1320),
    ("Vadasery, Nagercoil", 8.1830, 77.4300),
    ("Nagercoil Junction", 8.1790, 77.4330),
    ("Dindigul City", 10.3670, 77.9800),
    ("Dindigul Junction", 10.3610, 77.9700),
    ("Hosur Bus Stand", 12.7350, 77.8320),
    ("Hosur Industrial Area", 12.7400, 77.8050),
    ("Kanchipuram East", 12.8350, 79.7100),
    ("Kanchipuram West", 12.8330, 79.6950),
    ("Karur Bus Stand", 10.9600, 78.0800),
    ("Karur Junction", 10.9540, 78.0770),
    ("Ramanathapuram Town", 9.3640, 78.8390),
    ("Pudukkottai Town", 10.3820, 78.8170),
]

def patrol_records():
    chennai_units = [
        ("T. Nagar", 13.0460, 80.2290),
        ("Mylapore", 13.0370, 80.2680),
        ("Perambur", 13.1120, 80.2320),
    ]
    regional_units = [
        (address.split(",", 1)[0], latitude, longitude)
        for address, latitude, longitude in INCIDENT_LOCATIONS[8:]
    ]
    return [
        {
            "unit_id": f"SIM-TN-UNIT-{201 + index:03d}",
            "status": "TRAINING EXERCISE · AVAILABLE",
            "latitude": latitude,
            "longitude": longitude,
        }
        for index, (_, latitude, longitude) in enumerate(
            chennai_units + regional_units
        )
    ]


def station_records():
    chennai_stations = [
        ("T. Nagar Area Station", 13.0468, 80.2276, 201),
        ("Mylapore Area Station", 13.0380, 80.2667, 202),
        ("Perambur Area Station", 13.1113, 80.2334, 203),
    ]
    regional_stations = [
        (f"{address.split(',')[0]} Station", latitude, longitude, 204 + index)
        for index, (address, latitude, longitude) in enumerate(
            INCIDENT_LOCATIONS[8:]
        )
    ]
    return [
        {
            "station_id": f"SIM-TN-STATION-{station_number:03d}",
            "name": name,
            "latitude": latitude,
            "longitude": longitude,
            "unit_id": f"SIM-TN-UNIT-{station_number:03d}",
        }
        for name, latitude, longitude, station_number in (
            chennai_stations + regional_stations
        )
    ]


def seed_training_data():
    session = requests.Session()

    for patrol in patrol_records():
        response = session.post(
            f"{API_URL}/api/v1/patrol-units", json=patrol, timeout=10
        )
        response.raise_for_status()

    for station in station_records():
        response = session.post(
            f"{API_URL}/api/v1/police-stations", json=station, timeout=10
        )
        response.raise_for_status()

    print(
        "No incidents are seeded; public reports are stored when submitted. "
        f"{len(patrol_records())} patrol positions and "
        f"{len(station_records())} station records upserted via {API_URL}."
    )


if __name__ == "__main__":
    seed_training_data()
