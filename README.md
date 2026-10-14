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

Open the dashboard at <http://127.0.0.1:8501>. It polls the SQL-backed API
every two seconds. Newly detected critical incidents are queued for operator
review and shown as the active case on the map; they remain queued until an
operator records an alert. The dashboard also shows in-app toast alerts for
newly detected critical incidents. API documentation is at
<http://127.0.0.1:8000/docs>.

## Access from another computer on the same network

Run the API and dashboard on the computer that hosts the application. To make
the dashboard available to other computers on your trusted local network, start
Streamlit with a network-facing address:

```powershell
streamlit run frontend/app.py --server.address 0.0.0.0 --server.port 8501
```

On the host computer, find its local IPv4 address with `ipconfig`. From another
computer on the same network, open `http://<host-ipv4-address>:8501` (for
example, `http://192.168.1.50:8501`). If Windows Firewall prompts you, allow
Python/Streamlit on **private networks**. If it still cannot connect, allow
inbound TCP port 8501 on the host's private network. Do not enable public-network
access or router port forwarding for this prototype.

The dashboard normally calls the API on `127.0.0.1:8000` from the computer
running Streamlit, so the API can stay bound to localhost when the dashboard
and API run on the same host. If you also need to reach the API directly from
another computer, start it with:

```powershell
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Allow inbound TCP port 8000 only on the host's private network, then use
`http://<host-ipv4-address>:8000/docs` from the other computer. If Streamlit
runs on a different computer from the API, set `CRIME_RADAR_API_URL` on the
Streamlit computer to the API host's LAN address before starting Streamlit. For
example, in PowerShell:

```powershell
$env:CRIME_RADAR_API_URL = "http://192.168.1.50:8000"
streamlit run frontend/app.py
```

The API host must then listen on `0.0.0.0` and allow inbound connections on
port 8000.

This exposes the dashboard/API, not the MySQL server. The database should
remain accessible only to the API host; do not open MySQL port 3306 to the
network. If MySQL itself is on a separate server, configure its bind address,
firewall, and a dedicated account restricted to the API host, then set the
API's `CRIME_RADAR_DATABASE_URL` to that MySQL server's address.

## Configure MySQL

The API requires MySQL; SQLite is not supported. Create a MySQL database (for
example, `crime_radar`) and configure and verify the connection with the
helper. It includes a **Show password** option and saves the URL to the
git-ignored `.env` file only after a successful connection:

```powershell
.\.venv\Scripts\python.exe configure_mysql.py
```

Use a MySQL account with access to the selected database; a dedicated
application account is safer than `root`. Restart the FastAPI server after
configuration. On startup, SQLAlchemy creates the application tables in the
selected MySQL database. The API will not start without a valid
`mysql+asyncmy://` connection URL.
The dashboard uses an analytics / filters / map / incident-panels layout. The
map opens to a Tamil Nadu-wide view, with city-level focus options across the
state. It uses Esri street tiles with a dark tactical treatment, masks map
tiles outside Tamil Nadu, and restricts panning to the state's bounding area.
Esri basemap tiles are attributed in the map. Charts and the incident-density
heatmap reflect the currently displayed incidents. When training is disabled
and no agency data has arrived, they remain empty.

The dashboard's **Show incidents** switch is enabled by default. Incidents and
patrol and station positions are loaded from the configured MySQL database through the
API. Run the seeder below to populate 45 fixed incident records distributed
across Tamil Nadu, 40 patrol positions, and 40 station locations; the seeder
is safe to run again. The dashboard does not generate incidents locally.
Disable the switch to view only incidents received from an agency webhook.
The persistent
**NO LIVE AGENCY FEED** notice distinguishes the prototype display from an
authorized agency feed. The dashboard filters incidents by type, priority,
and status, and address search matches part of an address without case
sensitivity.
The **Most Reported Area** insight counts the leading area among the incidents
in the current filtered results and shows ties. Incident panels are sorted
newest first. New database-backed markers are added inside the existing
Leaflet map during dashboard refreshes, without rebuilding the map or
reloading its tiles. Selecting an incident alert smoothly moves the map to
that incident, highlights it with the red dot, and opens its popup; the
previous selected incident marker is removed from the map. Clicking an
incident also clears the blue incident-density heat spots from the map to
avoid leaving old hotspots behind during navigation.

The selected critical incident shows a 1.5 km containment ring; only one
ring is displayed at a time to keep overlapping zones readable. Intercept
lines use the Haversine distance to connect recently reported patrol units whose status is
`AVAILABLE` to the nearest matching agency incident.

With dashboard incidents enabled, critical events show an in-app alert and
are automatically assigned to the nearest available patrol marker (one unit
per event). Assignment details appear in the alert panel and incident map
popup. If no patrol telemetry is available in MySQL, the dashboard shows no patrol
markers.
These alerts and assignments never contact, notify, or dispatch real police.
Click a critical incident alert to center the map on that event and open its
incident popup. The persistent feed-status notice indicates that no live
agency feed is connected.

Critical incidents show the nearest station and patrol available in the
dashboard, with approximate Haversine distances. Manual assignment lets an
operator choose a station and available patrol; each patrol can be assigned
to only one active incident. Clear a manual assignment to return to automatic
matching. Choices are held in the current dashboard session and never contact
or dispatch real officers.
Overlapping incident markers appear as a plain location dot instead of a
numbered cluster; click it to zoom in and separate the incidents. Clicking an
incident shows its type, area, local report time, status, and nearest station
when available. Clicking a station shows its area and the nearest displayed
incident with report time and distance. The density heat layer uses a smaller,
softer radius to make busy locations easier to read. The left filters
dashboard slides open when hovered and closes when the pointer leaves. From an
incident's manual-assignment panel, **Record station message** records the
selected assignment and note in the session-only message log. It does not
send email, SMS, or contact a station; real messaging requires an authorized
dispatch integration.


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

To add the statewide training dataset to the configured MySQL database, start
FastAPI and run:

```powershell
python seed_training_data.py
```

The seeder is safe to run more than once: it inserts missing incidents and
upserts patrol and station records. Every inserted record is marked
`TRAINING EXERCISE`; it does not add or imply real reports. Station location
records populate the dashboard assignment controls. Station messages and
assignments are session records, not dispatches.
