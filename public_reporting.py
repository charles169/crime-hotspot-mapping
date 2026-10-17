import asyncio
import html
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import uuid4

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from backend.database import Base, SessionLocal, engine
from backend.geography import TAMIL_NADU_FEATURE, is_in_tamil_nadu
from backend.models import CrimeIncident

logger = logging.getLogger(__name__)
TURNSTILE_SITE_KEY = os.getenv("CRIME_RADAR_TURNSTILE_SITE_KEY", "").strip()
TURNSTILE_SECRET_KEY = os.getenv("CRIME_RADAR_TURNSTILE_SECRET_KEY", "").strip()
RATE_LIMIT_WINDOW_SECONDS = 15 * 60
RATE_LIMIT_MAX_ATTEMPTS = 5
MAX_REPORT_BODY_BYTES = 12_000
_submission_attempts: dict[str, deque[float]] = defaultdict(deque)
_submission_attempts_lock = threading.Lock()


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
    yield
    await engine.dispose()


app = FastAPI(
    title="C.R.I.M.E. Radar Public Reporter",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


class PublicReportSubmission(BaseModel):
    incident_type: str = Field(min_length=1, max_length=40)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    address: str = Field(default="", max_length=250)
    report_details: str = Field(default="", max_length=1000)
    website: str = Field(default="", max_length=200)
    turnstile_token: str = Field(min_length=1, max_length=4096)

    @field_validator("incident_type")
    @classmethod
    def validate_incident_type(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed_types = {
            "THEFT",
            "ASSAULT",
            "ROBBERY",
            "VEHICLE THEFT",
            "TRAFFIC ACCIDENT",
            "SUSPICIOUS ACTIVITY",
            "OTHER",
        }
        if normalized not in allowed_types:
            raise ValueError("Select a valid incident type.")
        return normalized

    @field_validator("address", "report_details")
    @classmethod
    def trim_optional_text(cls, value: str) -> str:
        return value.strip()


def consume_rate_limit(client_key: str) -> int | None:
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with _submission_attempts_lock:
        attempts = _submission_attempts[client_key]
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(_submission_attempts) > 10_000:
            for stale_key, stale_attempts in list(_submission_attempts.items()):
                while stale_attempts and stale_attempts[0] <= cutoff:
                    stale_attempts.popleft()
                if not stale_attempts and stale_key != client_key:
                    del _submission_attempts[stale_key]
        if (
            len(_submission_attempts) >= 10_000
            and client_key not in _submission_attempts
        ):
            return RATE_LIMIT_WINDOW_SECONDS
        if len(attempts) >= RATE_LIMIT_MAX_ATTEMPTS:
            return max(1, int(RATE_LIMIT_WINDOW_SECONDS - (now - attempts[0])))
        attempts.append(now)
        return None


async def verify_turnstile(token: str) -> bool:
    if not TURNSTILE_SITE_KEY or not TURNSTILE_SECRET_KEY:
        logger.error("Public reporting is disabled: Turnstile keys are not configured.")
        raise HTTPException(
            status_code=503,
            detail="Public reporting is not enabled yet.",
        )

    form_data = {
        "secret": TURNSTILE_SECRET_KEY,
        "response": token,
    }
    try:
        response = await asyncio.to_thread(
            requests.post,
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=form_data,
            timeout=(3, 5),
        )
        response.raise_for_status()
        verification = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Turnstile verification failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Verification is temporarily unavailable. Please try again.",
        ) from exc
    return bool(verification.get("success"))


@app.middleware("http")
async def add_security_headers_and_limit_body(request: Request, call_next):
    if request.url.path == "/api/reports":
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                too_large = int(content_length) > MAX_REPORT_BODY_BYTES
            except ValueError:
                too_large = True
            if too_large:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Report is too large."},
                )

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com "
        "https://challenges.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: blob: https://server.arcgisonline.com "
        "https://unpkg.com; "
        "connect-src 'self' https://challenges.cloudflare.com; "
        "frame-src https://challenges.cloudflare.com; "
        "form-action 'self'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors 'none'"
    )
    return response


@app.get("/", response_class=HTMLResponse)
async def public_report_form():
    site_key = html.escape(TURNSTILE_SITE_KEY, quote=True)
    geometry = TAMIL_NADU_FEATURE["geometry"]
    boundary_points = [
        point
        for polygon in geometry["coordinates"]
        for point in polygon[0]
    ]
    south = min(point[1] for point in boundary_points)
    west = min(point[0] for point in boundary_points)
    north = max(point[1] for point in boundary_points)
    east = max(point[0] for point in boundary_points)
    boundary_json = json.dumps(TAMIL_NADU_FEATURE, separators=(",", ":"))
    outside_mask_json = json.dumps(
        {
            "type": "Feature",
            "properties": {"name": "Outside Tamil Nadu"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [west - 15, south - 12],
                        [east + 15, south - 12],
                        [east + 15, north + 12],
                        [west - 15, north + 12],
                        [west - 15, south - 12],
                    ],
                    *[polygon[0] for polygon in geometry["coordinates"]],
                ],
            },
        },
        separators=(",", ":"),
    )
    if TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY:
        captcha_markup = (
            f'<div class="cf-turnstile" data-sitekey="{site_key}"></div>'
        )
        setup_notice = ""
        submit_disabled = ""
        turnstile_script = (
            '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" '
            "async defer></script>"
        )
    else:
        captcha_markup = ""
        setup_notice = (
            '<p class="notice">Public reporting is not enabled yet. '
            "The site owner must configure verification before reports can be sent.</p>"
        )
        submit_disabled = "disabled"
        turnstile_script = ""

    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#07111d">
  <title>Report an incident · C.R.I.M.E. Radar</title>
  <link rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
    crossorigin="">
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: #e3edf5; font: 16px/1.5 system-ui, sans-serif;
      background: radial-gradient(ellipse at 12% 0%, #12506b44, transparent 38%),
        radial-gradient(ellipse at 92% 16%, #493d7140, transparent 34%), #07111d; }}
    main {{ max-width: 820px; margin: 0 auto; padding: 38px 20px 56px; }}
    .portal-header {{ display: flex; align-items: center; gap: 16px;
      margin-bottom: 22px; }}
    .portal-brand-mark {{ display: grid; place-items: center; flex: 0 0 52px;
      width: 52px; height: 52px; color: #07111d; background:
      linear-gradient(145deg, #6be6f2, #27a9c0); border-radius: 15px;
      box-shadow: 0 0 26px #35c9dc38; font-size: 1.35rem; font-weight: 900; }}
    .portal-kicker {{ margin: 0 0 3px; color: #63d4e3; font-size: .68rem;
      font-weight: 800; letter-spacing: .15em; }}
    h1 {{ margin: 0; color: #f1f7fb; font-size: clamp(1.7rem, 5vw, 2.35rem);
      letter-spacing: -.04em; line-height: 1.1; }}
    .intro, .notice {{ color: #a8bdcc; line-height: 1.6; }}
    .intro {{ margin: 0 0 18px; }}
    .notice {{ padding: 14px 16px; background: #382b16;
      border: 1px solid #765b2a; border-radius: 12px; }}
    .emergency-note {{ padding: 13px 15px; margin: 14px 0 18px;
      color: #ffd9dd; background: linear-gradient(110deg, #35151c, #241722);
      border: 1px solid #703541; border-left: 4px solid #ff5364;
      border-radius: 10px; font-size: .92rem; }}
    form {{ display: grid; gap: 18px; padding: clamp(16px, 4vw, 28px);
      background: linear-gradient(145deg, #0e2030ed, #091724f2);
      border: 1px solid #24465b; border-radius: 18px;
      box-shadow: 0 22px 60px #0005; }}
    label {{ display: grid; gap: 7px; color: #d3e2ec; font-weight: 650; }}
    input, select, textarea, button {{ font: inherit; }}
    input, select, textarea {{ width: 100%; min-height: 46px; padding: 11px 13px;
      color: #e3edf5; background: #0a1927; border: 1px solid #315064;
      border-radius: 10px; transition: border-color .16s ease, box-shadow .16s ease; }}
    input:focus, select:focus, textarea:focus {{ outline: none;
      border-color: #35c9dc; box-shadow: 0 0 0 3px #35c9dc22; }}
    textarea {{ min-height: 110px; resize: vertical; }}
    #report-map {{ height: 440px; border: 1px solid #315064; border-radius: 13px;
      box-shadow: 0 10px 30px #0004; background: #07111d; }}
    #report-map .leaflet-tile-pane {{ filter: brightness(.27) saturate(1.9)
      contrast(1.35) hue-rotate(170deg); }}
    .coordinates {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    button {{ min-height: 46px; padding: 10px 16px; color: #06131b;
      background: linear-gradient(135deg, #68e0ed, #35b8cf); border: 1px solid #55d7e4;
      border-radius: 10px; font-weight: 750; cursor: pointer;
      transition: transform .16s ease, box-shadow .16s ease, filter .16s ease; }}
    button:hover:not(:disabled) {{ transform: translateY(-1px);
      box-shadow: 0 9px 24px #35c9dc2b; filter: brightness(1.05); }}
    button.secondary {{ color: #d7e4ef; background: #142b3c;
      border-color: #315064; }}
    button.location-action {{ width: 100%; min-height: 56px; font-size: 1rem; }}
    button:disabled {{ opacity: .55; cursor: not-allowed; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; }}
    .actions button {{ flex: 1 1 190px; }}
    #result {{ min-height: 24px; margin: 12px 2px 0; color: #a8bdcc;
      line-height: 1.5; }}
    .portal-footnote {{ margin: 14px 2px 0; color: #8199aa; font-size: .8rem; }}
    .honeypot {{ position: absolute; left: -10000px; width: 1px; height: 1px;
      overflow: hidden; }}
    @media (max-width: 520px) {{
      main {{ padding: 24px 12px 36px; }}
      .coordinates {{ grid-template-columns: 1fr; }}
      #report-map {{ height: min(380px, 58vh); }}
      .portal-brand-mark {{ width: 44px; height: 44px; flex-basis: 44px; }}
      .actions button {{ flex-basis: 100%; }}
    }}
  </style>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
    crossorigin=""></script>
  {turnstile_script}
</head>
<body>
  <main>
    <header class="portal-header">
      <div class="portal-brand-mark" aria-hidden="true">R</div>
      <div>
        <p class="portal-kicker">C.R.I.M.E. RADAR · TAMIL NADU</p>
        <h1>Report an incident</h1>
      </div>
    </header>
    <p class="intro">Share what you saw and help build a clearer picture of
    reported activity across Tamil Nadu.</p>
    <p class="emergency-note"><strong>Emergency? Call 112.</strong> This form
    does not contact police.</p>
    {setup_notice}
    <form id="report-form">
      <label>What did you see?
        <select name="incident_type" required>
          <option>THEFT</option>
          <option>ASSAULT</option>
          <option>ROBBERY</option>
          <option>VEHICLE THEFT</option>
          <option>TRAFFIC ACCIDENT</option>
          <option>SUSPICIOUS ACTIVITY</option>
          <option>OTHER</option>
        </select>
      </label>
      <button class="location-action" id="locate-button" type="button">
        Use my current location
      </button>
      <div>
        <label for="report-map">Or tap the map to mark the location</label>
        <div id="report-map" aria-label="Select incident location on map"></div>
      </div>
      <div class="actions">
        <button class="secondary" id="share-button" type="button">
          Share this report page
        </button>
      </div>
      <label>Nearby place or landmark (optional)
        <input name="address" maxlength="250"
          placeholder="e.g. bus stand, street, or area">
      </label>
      <div class="coordinates">
        <label>Latitude
          <input name="latitude" type="number" step="any" min="6" max="14"
            required placeholder="Tap the map">
        </label>
        <label>Longitude
          <input name="longitude" type="number" step="any" min="76" max="81"
            required placeholder="Tap the map">
        </label>
      </div>
      <label>Brief details (optional)
        <textarea name="report_details" maxlength="1000"
          placeholder="Add any useful details"></textarea>
      </label>
      <label class="honeypot" aria-hidden="true">Leave this field empty
        <input name="website" tabindex="-1" autocomplete="off">
      </label>
      {captcha_markup}
      <button type="submit" {submit_disabled}>Submit unverified report</button>
    </form>
    <p id="result" role="status" aria-live="polite"></p>
    <p class="portal-footnote">Reports are stored for operator review. They are
    unverified and do not dispatch emergency services.</p>
  </main>
  <script>
    const tamilNaduBoundary = {boundary_json};
    const outsideMask = {outside_mask_json};
    const map = L.map("report-map", {{
      minZoom: 6,
      maxBounds: [[{south - 0.2}, {west - 0.2}], [{north + 0.2}, {east + 0.2}]],
      maxBoundsViscosity: 1.0
    }}).setView([10.8, 78.2], 6);
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/" +
      "World_Street_Map/MapServer/tile/{{z}}/{{y}}/{{x}}",
      {{maxZoom: 16, maxNativeZoom: 16,
        attribution: "Tiles © Esri — Sources: Esri, HERE, Garmin, USGS, " +
          "OpenStreetMap contributors, and the GIS User Community"}}
    ).addTo(map);
    L.geoJSON(outsideMask, {{
      style: {{color: "#07111d", weight: 0, fillColor: "#07111d", fillOpacity: .88}},
      interactive: false
    }}).addTo(map);
    L.geoJSON(tamilNaduBoundary, {{
      style: {{color: "#20c6df", weight: 3, opacity: .95,
        fillColor: "#0b1b2a", fillOpacity: .06}},
      interactive: false
    }}).addTo(map);
    let marker = null;
    function setLocation(lat, lng) {{
      document.querySelector('[name="latitude"]').value = lat.toFixed(6);
      document.querySelector('[name="longitude"]').value = lng.toFixed(6);
      document.getElementById("result").textContent =
        "Location selected. Choose the incident type and submit.";
      if (marker) marker.setLatLng([lat, lng]);
      else marker = L.marker([lat, lng]).addTo(map);
    }}
    map.on("click", event => setLocation(event.latlng.lat, event.latlng.lng));
    document.getElementById("share-button").addEventListener("click", async () => {{
      const shareData = {{
        title: "Report an incident",
        text: "Quick incident report for Tamil Nadu",
        url: window.location.href
      }};
      try {{
        if (navigator.share) await navigator.share(shareData);
        else {{
          await navigator.clipboard.writeText(window.location.href);
          document.getElementById("result").textContent =
            "Report link copied. Share it with others.";
        }}
      }} catch (error) {{
        if (error.name !== "AbortError") {{
          document.getElementById("result").textContent =
            "Could not share automatically. Copy this page's address to share it.";
        }}
      }}
    }});
    document.getElementById("locate-button").addEventListener("click", () => {{
      if (!navigator.geolocation) {{
        document.getElementById("result").textContent =
          "Location is unavailable; tap the map to mark the incident instead.";
        return;
      }}
      navigator.geolocation.getCurrentPosition(
        position => {{
          const {{latitude, longitude}} = position.coords;
          map.setView([latitude, longitude], 15);
          setLocation(latitude, longitude);
        }},
        () => {{
          document.getElementById("result").textContent =
            "Could not get your location; tap the map to mark the incident.";
        }},
        {{enableHighAccuracy: true, timeout: 10000}}
      );
    }});
    document.getElementById("report-form").addEventListener("submit", async event => {{
      event.preventDefault();
      const form = event.currentTarget;
      const result = document.getElementById("result");
      const submit = form.querySelector('[type="submit"]');
      const values = new FormData(form);
      const token = window.turnstile
        ? window.turnstile.getResponse()
        : "";
      if (!token) {{
        result.textContent = "Complete the verification before submitting.";
        return;
      }}
      const body = Object.fromEntries(values.entries());
      body.latitude = Number(body.latitude);
      body.longitude = Number(body.longitude);
      body.turnstile_token = token;
      submit.disabled = true;
      result.textContent = "Submitting report…";
      try {{
        const response = await fetch("/api/reports", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify(body)
        }});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Report could not be saved.");
        form.reset();
        if (marker) {{ map.removeLayer(marker); marker = null; }}
        if (window.turnstile) window.turnstile.reset();
        result.textContent =
          "Report received and saved as unverified. Reference: " + payload.event_id;
      }} catch (error) {{
        result.textContent = error.message +
          " Your report was not confirmed as saved; please retry.";
        if (window.turnstile) window.turnstile.reset();
      }} finally {{
        submit.disabled = false;
      }}
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(page)


@app.post("/api/reports", status_code=201)
async def submit_public_report(report: PublicReportSubmission, request: Request):
    if report.website.strip():
        raise HTTPException(status_code=400, detail="Report could not be accepted.")

    client_ip = request.client.host if request.client else "unknown"
    retry_after = consume_rate_limit(client_ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please wait before trying again.",
            headers={"Retry-After": str(retry_after)},
        )

    if not await verify_turnstile(report.turnstile_token):
        raise HTTPException(
            status_code=400,
            detail="Verification failed. Please complete the challenge and retry.",
        )
    if not is_in_tamil_nadu(report.latitude, report.longitude):
        raise HTTPException(
            status_code=422,
            detail="The selected location must be inside Tamil Nadu.",
        )
    allowed_types = {
        "THEFT",
        "ASSAULT",
        "ROBBERY",
        "VEHICLE THEFT",
        "TRAFFIC ACCIDENT",
        "SUSPICIOUS ACTIVITY",
        "OTHER",
    }
    if report.incident_type not in allowed_types:
        raise HTTPException(status_code=422, detail="Select a valid incident type.")

    event_id = f"REPORT-{uuid4().hex.upper()}"
    db_incident = CrimeIncident(
        event_id=event_id,
        status="PENDING_REVIEW",
        priority="UNVERIFIED",
        timestamp=datetime.utcnow(),
        address=report.address
        or f"Location pin {report.latitude:.5f}, {report.longitude:.5f}",
        report_details=report.report_details or None,
        **report.model_dump(
            exclude={
                "turnstile_token",
                "website",
                "address",
                "report_details",
            },
        ),
    )
    try:
        async with SessionLocal() as db:
            db.add(db_incident)
            await db.commit()
    except SQLAlchemyError as exc:
        logger.error("Could not save public report: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Report could not be saved right now. Please try again.",
        ) from exc

    logger.info("Public report saved for operator review: %s", event_id)
    return {
        "status": "received",
        "event_id": event_id,
        "priority": "UNVERIFIED",
        "message": "Report saved for operator review.",
    }
