# C.R.I.M.E. Radar

C.R.I.M.E. (Coordinated Response & Incident Mapping Engine) is a prototype
incident and patrol telemetry dashboard restricted to Tamil Nadu, India.
Incoming incident and patrol coordinates are checked against the Tamil Nadu
state boundary; outside locations are rejected and legacy out-of-state records
are removed at API startup.

## Data source and limitations

The app does **not** connect to Tamil Nadu Police CAD/911, FIR, or patrol GPS
systems, and no verified public, geocoded live incident feed is configured.
Real agency data appears only after an authorized provider posts it to the
documented webhooks. Do not treat simulator data as real events.

The Tamil Nadu map boundary comes from geoBoundaries India ADM1, based on the
DataMeet maps source, under CC BY 2.5 IN. Attribution: [geoBoundaries](https://www.geoboundaries.org/).

## Run locally

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Start the API and dashboard in separate terminals:

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
streamlit run frontend/app.py
```

Open the dashboard at <http://127.0.0.1:8501>. It refreshes every five
seconds. The dashboard shows in-app toast alerts for newly detected critical
incidents. API documentation is at <http://127.0.0.1:8000/docs>.

## Configure MySQL

The API can use either its default local SQLite database or a MySQL database.
Install the requirements above and create a MySQL database (for example,
`crime_radar`). Configure and verify the connection with the helper's window.
It includes a **Show password** option and saves the URL to the git-ignored
`.env` file only after a successful connection:

```powershell
.\.venv\Scripts\python.exe configure_mysql.py
```

Use a MySQL account with access to the selected database; a dedicated
application account is safer than `root`. Restart the FastAPI server after
configuration. On startup, SQLAlchemy creates the application tables in the
selected database. If no `.env` setting is present, the API uses
`crime_radar_core.db` (SQLite).
The dashboard uses an analytics / filters / map / incident-panels layout. The
map opens at detailed Chennai street level and can jump to other Tamil Nadu
cities. It uses Esri street tiles with a dark tactical treatment, masks map
tiles outside Tamil Nadu, and restricts panning to the state's bounding area.
Esri basemap tiles are attributed in the map. Charts and the incident-density
heatmap reflect the currently displayed incidents. When training is disabled
and no agency data has arrived, they remain empty.

The dashboard's **Show training exercise** switch is enabled by default to
display map markers, hotspot density, and incident panels. The eight
Chennai training examples are created in the dashboard only and are never
saved to the API database. The included simulator also writes training records
to the API; these are hidden when the training switch is off. Neither source
represents live reports. Disable the switch to view only incidents received
from an agency webhook.
While the switch is on, the dashboard adds a new in-memory Chennai training
incident approximately every ten seconds, retains at most 20 generated
incidents, and stops generating them when the switch is off. These incidents
are not written to MySQL or presented as agency records. New markers are added
inside the existing Leaflet map, so the map and its tiles stay in place during
the five-second dashboard refresh. Selecting an incident alert smoothly moves
the map to that incident, highlights it with the red dot, and opens its popup;
the previous selected incident marker is removed from the map. Clicking an
incident also clears the blue incident-density heat spots from the map to
avoid leaving old hotspots behind during navigation.

Critical incidents receive a 1.5 km containment ring. Intercept lines use the
Haversine distance to connect recently reported patrol units whose status is
`AVAILABLE` to the nearest matching agency incident. Training incidents and
patrol positions are not used to imply a real dispatch.

With the training exercise enabled, critical events show an in-app alert and
are automatically assigned to the nearest available training patrol marker
(one unit per event). Assignment details appear in the alert panel and
incident map popup. If no training patrol telemetry is running, the dashboard
uses clearly labeled, frontend-only training patrol markers. These alerts and
assignments never contact, notify, or dispatch real police.
Click a critical incident alert to center the map on that event and open its
incident popup. Training activity is visibly marked and is not live agency
data.


## Ingest authorized agency telemetry

Post incidents to `POST /api/v1/ingest`:

```json
{
  "event_id": "TN-EVENT-1001",
  "status": "DISPATCHED",
  "priority": "CRITICAL",
  "incident_type": "BURGLARY",
  "latitude": 13.0827,
  "longitude": 80.2707,
  "address": "Chennai, Tamil Nadu"
}
```

Post patrol positions to `POST /api/v1/patrol-units` with `unit_id`, `status`,
`latitude`, and `longitude`. Both endpoints reject coordinates outside Tamil
Nadu. Patrol positions older than two minutes are not shown.

Run `python simulator.py` only to generate clearly labeled training data around
Chennai; it does not represent police activity.
