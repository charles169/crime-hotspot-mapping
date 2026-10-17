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
every two seconds so submitted public reports appear promptly. Cases rotate
automatically every five seconds in the incident alert panel, and previous
cases remain queued there with controls to show their location and send a
simulated station alert. Nearby stations appear as blue PD badges and incidents
as red dots on the map. Operators can choose a station from the incident's
nearby-station list before sending a simulated alert. Map movement happens only
when an operator chooses to show a case location.
The dashboard also shows in-app toast alerts for newly detected critical
incidents. API documentation is at
<http://127.0.0.1:8000/docs>.

The **Crime trend analytics** panel compares daily incident counts, incident
types, and priorities across a selected period and the preceding equal-length
period. Simulated/demo records are excluded by default. Stored public reports
are unverified, and the charts describe recorded reports rather than forecast
crime; no verified live agency feed is configured. It flags unusual days when
reports reach at least three and exceed both twice the prior seven-day average
and that average by at least three. Higher-severity flags use a five-report,
three-times / five-report increase threshold. These are review cues only, not
predictions or verification of an incident.

## Dashboard operator login

The Streamlit dashboard requires a single operator account configured in the
git-ignored `.env` file. Add a unique username and a strong password there:

```dotenv
CRIME_RADAR_LOGIN_USERNAME=operator
CRIME_RADAR_LOGIN_PASSWORD=replace-with-a-strong-local-password
```

Restart the dashboard after changing these values. Do not commit `.env` or
reuse the MySQL password for dashboard access. This login gates the Streamlit
dashboard; keep the API bound to `127.0.0.1` unless API authentication is added
before exposing it to other machines.

## Separate public report portal prototype

`public_reporting.py` serves a standalone, mobile-friendly report-only page at
`/` and accepts submissions at `/api/reports`. Reporters can use their current
location or tap the map; a landmark and details are optional. The page provides
a native share action so its link can be sent directly or printed as a QR code.
It writes reports to the shared MySQL database as **PENDING_REVIEW /
UNVERIFIED**, where they appear in the private operator dashboard for review.
It does not expose the dashboard's incident, patrol, station, or
agency-ingestion API routes.

Run it locally for development with:

```powershell
python -m uvicorn public_reporting:app --host 127.0.0.1 --port 8001
```

The public page requires both `CRIME_RADAR_TURNSTILE_SITE_KEY` and
`CRIME_RADAR_TURNSTILE_SECRET_KEY`; without them, submission stays disabled.
Configure them locally without putting the secret in chat or Git by running:

```powershell
.\.venv\Scripts\python.exe configure_turnstile.py
```

The helper prompts for the site key and hidden secret, then updates the
git-ignored `.env` file while preserving other settings.
The configured Funnel address can be shared as a link or encoded as a QR code
on community posters. For a public preview through Tailscale, enable Funnel for
the device and run:

```powershell
tailscale funnel --bg 8001
```

Funnel must point only to this report-only service; do not publish ports 8000
(API) or 8501 (dashboard). Add the Funnel hostname to the allowed hostnames
for your Cloudflare Turnstile widget, then put its site and secret keys in the
git-ignored `.env` file and restart `public_reporting.py`. Never share or
commit the secret key. Keep the operator dashboard, main API, and MySQL
private. The included per-process rate limit is only a fallback; a production
service also needs proxy/edge rate limiting. Reports are unverified and do not
contact police or replace emergency services; call 112 in India for
emergencies. Do not include sensitive personal information.

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

### Use the MySQL data on another computer without networking the database

Install and run MySQL locally on each computer, and configure the app on each
computer with host `127.0.0.1`. This keeps MySQL reachable only from that same
computer. Use a database export to carry the data between computers; this is a
snapshot, not live synchronization. Only use one copy at a time, and repeat
the export/import whenever you move the latest data.

On the computer that currently has the latest data, open **Command Prompt**
and export the database (replace `crime_radar` if you chose a different name):

```text
mysqldump -h 127.0.0.1 -P 3306 -u root -p --single-transaction --databases crime_radar --result-file=crime_radar.sql
```

Enter the local MySQL administrator password when prompted. Transfer
`crime_radar.sql` to the other computer using a USB drive or another secure
method. On that computer, first install/start MySQL and create the local
application account with access to the `crime_radar` database. Then import
the dump from **Command Prompt**:

```text
mysql -h 127.0.0.1 -P 3306 -u root -p < crime_radar.sql
```

Run `configure_mysql.py` there and use host `127.0.0.1` and the local database
account. MySQL user accounts are not included in the database dump, so create
the application account separately on each computer. Before importing a
newer snapshot over a database that already has data, back up that database
and replace it deliberately; importing is not a merge. Treat the dump as
sensitive data and do not put it in a public folder or repository.

The dashboard uses an analytics / filters / map / incident-panels layout. The
map opens to a Tamil Nadu-wide view, with city-level focus options across the
state. It uses Esri's dark-gray basemap, masks map
tiles outside Tamil Nadu, and restricts panning to the state's bounding area.
Esri basemap tiles are attributed in the map.

The dashboard's **Show incidents** switch is enabled by default. Public reports
submitted through the report portal are stored as `PENDING_REVIEW` /
`UNVERIFIED` in MySQL and polled by the dashboard every three seconds. New reports
appear on the map and in the incident queue without requiring a page reload.
Patrol and station positions are loaded from MySQL; the reference-data seeder
below does not create incident records. Presentation/demo incident seeders are
disabled by default. To add 300 clearly marked synthetic records for a
dashboard demonstration, run:

```powershell
python seed_presentation_incidents.py
```

These records are labeled `SIMULATED DEMO` / `DEMO`, use synthetic locations,
and are not real incidents. The script is safe to rerun; it fills in any
missing showcase records without duplicating the 300 demo event IDs. Do not use
them as real incident or public-safety data.
It presents a single incident at a time and waits for the operator to send its
alert before advancing; new public user reports are queued immediately after
the active incident. The presentation dataset does not change public reports.
Disable the switch to view only incidents received from an agency webhook.
The persistent
**NO LIVE AGENCY FEED** notice distinguishes the prototype display from an
authorized agency feed. The dashboard filters incidents by type, priority,
and status, and address search matches part of an address without case
sensitivity.
The **Most Reported Area** insight counts the leading area among the incidents
in the current filtered results and shows ties. Incident panels are sorted
newest first. Incident markers are updated inside the existing Leaflet map
during dashboard refreshes without rebuilding the map or reloading its tiles.
Use **Show location on map** on the active incident card to center the map on
that incident, highlight it, and open its popup.

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
The persistent feed-status notice indicates that no live
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
incident with report time and distance. The left filters
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

To populate only reference patrol and station positions in the configured
MySQL database, start FastAPI and run:

```powershell
python seed_training_data.py
```

The seeder is safe to run more than once and only upserts patrol and station
records. Incident rows come from public reports or authorized agency ingestion.
Station messages and assignments are session records, not dispatches.
