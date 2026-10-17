import asyncio
import html
import json
import os
import time
from datetime import datetime, timedelta, timezone
from math import asin, ceil, cos, floor, radians, sin, sqrt
from pathlib import Path

import folium
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from branca.element import MacroElement, Template
from folium.plugins import HeatMap, MarkerCluster

with (Path(__file__).resolve().parents[1] / "backend" / "data" / "tamil_nadu.geojson").open(
    encoding="utf-8"
) as boundary_file:
    TAMIL_NADU_FEATURE = json.load(boundary_file)


class TacticalTileStyle(MacroElement):
    _template = Template(
        """{% macro html(this, kwargs) %}
        <style>.leaflet-container {background:#07111d;}
        .leaflet-heatmap-layer {filter:drop-shadow(0 0 9px rgba(255,98,42,.6));}
        .leaflet-fade-anim .leaflet-tile,
        .leaflet-fade-anim .leaflet-popup {transition:none !important;}
        .incident-cluster-icon {align-items:center;background:#35151c;
            border:2px solid #ff5364;border-radius:50%;box-shadow:0 0 10px #ff354d66;
            display:flex;height:34px;justify-content:center;width:34px;}
        .incident-cluster-dot {background:#ff354d;border:2px solid #fff;
            border-radius:50%;box-shadow:0 0 8px #ff354d99;height:12px;width:12px;}
        .incident-map-marker {background:#ff354d;border:2px solid #fff;
            border-radius:50%;box-shadow:0 0 8px #ff354d99;height:12px;
            position:relative;width:12px;}
        .incident-map-marker::after {background:#fff;border-radius:50%;content:"";
            height:4px;left:50%;position:absolute;top:50%;
            transform:translate(-50%,-50%);width:4px;}
        .incident-map-marker.selected {background:#ff354d;border:3px solid #fff;
            box-shadow:0 0 12px #ff354d;}
        .leaflet-popup-content-wrapper {background:#081a2b;
            border:1px solid #2c9bb1;border-radius:7px;
            box-shadow:0 0 18px rgba(20,190,220,.2);color:#d7eaf1;}
        .leaflet-popup-content {margin:0;max-width:310px;width: min(310px, 72vw);}
        .leaflet-popup-tip {background:#081a2b;border:1px solid #2c9bb1;}
        .leaflet-popup-close-button {color:#66d8e9 !important;}
        .incident-hud-content {color:#d7eaf1;font-family:Arial,sans-serif;
            padding:12px;}
        .incident-hud-kicker {color:#59d6e8;font-size:10px;font-weight:700;
            letter-spacing:1.5px;text-transform:uppercase;}
        .incident-hud-title {color:#effaff;font-size:16px;font-weight:700;
            line-height:1.25;margin:5px 0 10px;overflow-wrap:anywhere;}
        .incident-hud-grid {display:grid;grid-template-columns:1fr 1fr;gap:7px;}
        .incident-hud-field {background:#0b2437;border-left:2px solid #197b91;
            min-width:0;padding:6px 8px;}
        .incident-hud-field-wide {grid-column:1/-1;}
        .incident-hud-label {color:#70aabc;display:block;font-size:9px;
            font-weight:700;letter-spacing:1px;text-transform:uppercase;}
        .incident-hud-value {color:#e0f1f5;display:block;font-size:12px;
            line-height:1.4;margin-top:3px;overflow-wrap:anywhere;}
        .incident-hud-state {display:flex;flex-wrap:wrap;gap:6px;margin-top:9px;}
        .incident-hud-state span {background:#102d40;border:1px solid #22677c;
            border-radius:3px;color:#89e5ef;font-size:9px;font-weight:700;
            letter-spacing:.7px;padding:3px 6px;}
        .incident-hud-report,.incident-hud-extra {border-top:1px solid #174054;
            color:#a8cbd5;font-size:11px;line-height:1.5;margin-top:9px;
            padding-top:8px;overflow-wrap:anywhere;}
        .police-station-badge {align-items:center;background:#0756a0;
            border:2px solid #fff;clip-path:polygon(50% 0,95% 15%,88% 65%,
            50% 100%,12% 65%,5% 15%);color:#fff;display:flex;
            filter:drop-shadow(0 0 5px #168bd2);font:700 9px Arial,sans-serif;
            height:30px;justify-content:center;width:28px;}
        </style>
        {% endmacro %}"""
    )


class IncidentMarkerRegistry(MacroElement):
    _template = Template(
        """{% macro script(this, kwargs) %}
        window.crimeRadarMarkers = window.crimeRadarMarkers || {};
        window.crimeRadarMarkers[{{this.event_id_json}}] = {
          marker: {{this.marker.get_name()}},
          normalIcon: {{this.marker.get_name()}}.getIcon()
        };
        {% endmacro %}"""
    )

    def __init__(self, event_id, marker):
        super().__init__()
        self._name = "IncidentMarkerRegistry"
        self.event_id_json = json.dumps(event_id)
        self.marker = marker


class IncidentClusterRegistry(MacroElement):
    _template = Template(
        """{% macro script(this, kwargs) %}
        window.crimeRadarIncidentMarkerCluster = {{this.cluster.get_name()}};
        {% endmacro %}"""
    )

    def __init__(self, cluster):
        super().__init__()
        self._name = "IncidentClusterRegistry"
        self.cluster = cluster


class MapFocusWatcher(MacroElement):
    _template = Template(
        """{% macro script(this, kwargs) %}
        (function () {
          const map = {{this._parent.get_name()}};
          const city = {{this.city_json}};
          const viewKey = "crime-radar-map-view";
          const savedView = window.parent.sessionStorage.getItem(viewKey);
          if (savedView) {
            const view = JSON.parse(savedView);
            if (view.city === city) {
              map.setView(view.center, view.zoom, {animate: false});
            }
          }
          map.on("moveend", function () {
            window.parent.sessionStorage.setItem(viewKey, JSON.stringify({
              city: city,
              center: map.getCenter(),
              zoom: map.getZoom()
            }));
          });
          window.crimeRadarMarkers = window.crimeRadarMarkers || {};
          window.crimeRadarSelectedMarkerId =
            window.crimeRadarSelectedMarkerId || null;
          const incidentCluster = window.crimeRadarIncidentMarkerCluster;
          const trainingMarkers = {};
          let lastPayload = "";
          let lastFocus = "";
          const syncMapState = function () {
            const payload =
              window.parent.sessionStorage.getItem("crime-radar-training-cases") || "[]";
            if (payload !== lastPayload) {
              const incoming = JSON.parse(payload);
              const activeIds = new Set();
              incoming.forEach(function (incident) {
                const id = String(incident.event_id);
                activeIds.add(id);
                if (trainingMarkers[id]) {
                  const existingMarker = window.crimeRadarMarkers[id] &&
                    window.crimeRadarMarkers[id].marker;
                  if (existingMarker && trainingMarkers[id].popupHtml !== incident.popup_html) {
                    existingMarker.setPopupContent(incident.popup_html);
                    trainingMarkers[id].popupHtml = incident.popup_html;
                  }
                  return;
                }
                const position = [incident.latitude, incident.longitude];
                const normalIcon = L.divIcon({
                  className: "",
                  html: '<div class="incident-map-marker"></div>',
                  iconSize: [18, 18],
                  iconAnchor: [9, 9]
                });
                const marker = L.marker(position, {
                  icon: normalIcon,
                  title: incident.incident_type
                }).bindPopup(incident.popup_html);
                incidentCluster.addLayer(marker);
                trainingMarkers[id] = {
                  marker: marker,
                  popupHtml: incident.popup_html
                };
                window.crimeRadarMarkers[id] = {
                  marker: marker,
                  normalIcon: normalIcon
                };
              });
              Object.keys(trainingMarkers).forEach(function (id) {
                if (activeIds.has(id)) return;
                incidentCluster.removeLayer(trainingMarkers[id].marker);
                delete trainingMarkers[id];
                delete window.crimeRadarMarkers[id];
              });
              lastPayload = payload;
            }
            const focusState = JSON.parse(
              window.parent.sessionStorage.getItem("crime-radar-map-focus") || "{}"
            );
            const focus = focusState.event_id || "";
            const focusToken = focus + "|" + (focusState.nonce || "");
            if (!focus || focusToken === lastFocus) return;
            const entry = window.crimeRadarMarkers &&
              window.crimeRadarMarkers[focus];
            if (entry) {
              const previousId = window.crimeRadarSelectedMarkerId;
              const previouslySelected = window.crimeRadarMarkers[previousId];
              if (previouslySelected && previousId !== focus) {
                previouslySelected.marker.setIcon(previouslySelected.normalIcon);
              }
              const marker = entry.marker;
              marker.setIcon(L.divIcon({
                className: "",
                html: '<div class="incident-map-marker selected"></div>',
                iconSize: [22, 22],
                iconAnchor: [11, 11]
              }));
              window.crimeRadarSelectedMarkerId = focus;
              lastFocus = focusToken;
              map.flyTo(marker.getLatLng(), Math.max(map.getZoom(), 16), {
                animate: true,
                duration: 1.1,
                easeLinearity: 0.25
              });
              marker.openPopup();
            }
          };
          window.parent.document.addEventListener(
            "crime-radar-map-update",
            syncMapState
          );
          syncMapState();
          window.setInterval(syncMapState, 100);
        })();
        {% endmacro %}"""
    )

    def __init__(self, city):
        super().__init__()
        self._name = "MapFocusWatcher"
        self.city_json = json.dumps(city)


st.set_page_config(
    layout="wide",
    page_title="C.R.I.M.E. Radar | Tamil Nadu",
    page_icon="📡",
    initial_sidebar_state="collapsed",
)

def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def nearest_training_station(latitude, longitude, stations):
    if not stations:
        return None, None
    station = min(
        stations,
        key=lambda item: haversine(
            item["longitude"], item["latitude"], longitude, latitude
        ),
    )
    distance_km = haversine(
        station["longitude"], station["latitude"], longitude, latitude
    )
    return station, distance_km


def nearest_training_stations(
    latitude,
    longitude,
    stations,
    max_count=5,
    exclude_station_id=None,
    max_distance_km=None,
):
    if not stations:
        return []
    sorted_stations = sorted(
        stations,
        key=lambda item: haversine(
            item["longitude"], item["latitude"], longitude, latitude
        ),
    )
    results = []
    for station in sorted_stations:
        if station["station_id"] == exclude_station_id:
            continue
        distance_km = haversine(
            station["longitude"], station["latitude"], longitude, latitude
        )
        if max_distance_km is not None and distance_km > max_distance_km:
            break
        results.append((station, distance_km))
        if len(results) >= max_count:
            break
    return results


def focus_incident_on_map(event_id):
    st.session_state["map_focus_event_id"] = str(event_id)
    st.session_state["route-planner-incident"] = str(event_id)
    st.session_state["map_focus_nonce"] = str(time.time_ns())


def record_incident_station_alert(
    incident_id,
    incident_type,
    address,
    latitude,
    longitude,
    station_name,
    distance_km,
):
    incident_queue = st.session_state.setdefault("incident_action_queue", [])
    previous_incidents = st.session_state.setdefault(
        "previous_action_incidents", []
    )
    if (
        incident_id not in incident_queue
        and not any(
            str(item["event_id"]) == str(incident_id)
            for item in previous_incidents
        )
    ):
        return

    st.session_state["last_auto_case_rotation_at"] = time.monotonic()
    outbox = st.session_state.setdefault("station_alert_outbox", [])
    outbox.append(
        {
            "sent_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "station_name": station_name,
            "incident_id": incident_id,
            "incident_type": incident_type,
            "distance_km": f"{distance_km:.2f}",
            "message": (
                f"Incident: {incident_type} at {address} "
                f"({latitude:.5f}, {longitude:.5f})."
            ),
            "status": "SIMULATED — NOT SENT",
        }
    )
    del outbox[:-50]
    if incident_id in incident_queue:
        incident_queue.remove(incident_id)
    previous_incidents[:] = [
        item
        for item in previous_incidents
        if str(item["event_id"]) != str(incident_id)
    ]
    if incident_queue:
        next_incident_id = str(incident_queue[0])
        st.session_state.setdefault("released_action_incident_ids", set()).add(
            next_incident_id
        )
    st.session_state["station_alert_confirmation"] = (
        f"Alert message sent to {station_name} (simulated)."
    )


def is_training_status(status):
    return str(status).strip().upper().startswith(("SIMULATED DEMO", "TRAINING EXERCISE"))


def display_incident_status(status):
    return "OPEN" if is_training_status(status) else str(status)


def display_incident_type(incident_type, is_training):
    value = str(incident_type)
    if is_training and value.strip().upper() == "SIMULATED DEMO EVENT":
        return "INCIDENT"
    return value


def display_incident_address(address, is_training):
    value = str(address)
    if is_training:
        for suffix in (" (simulated location)", " · SYNTHETIC DEMO"):
            value = value.replace(suffix, "")
    return value


def display_event_id(event_id, is_training):
    value = str(event_id)
    if is_training and value.upper().startswith(("SIM-", "DEMO-", "TRAIN-")):
        return f"TN-{value.rsplit('-', 1)[-1]}"
    return value


def display_incident_time(value):
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(timestamp):
        return "Time unavailable"
    local_time = timestamp.to_pydatetime().astimezone(
        timezone(timedelta(hours=5, minutes=30))
    )
    return local_time.strftime("%d %b %Y, %I:%M %p IST")


def incident_hud_html(incident, extra_html="", is_active=False):
    status = str(incident.get("status", "UNKNOWN"))
    is_demo = bool(incident.get("is_demo", is_training_status(status)))
    incident_type = html.escape(
        display_incident_type(incident.get("incident_type", "INCIDENT"), is_demo)
    )
    address = html.escape(
        display_incident_address(incident.get("address", "Location unavailable"), is_demo)
    )
    event_id = html.escape(
        display_event_id(incident.get("event_id", "Unknown"), is_demo)
    )
    priority = html.escape(str(incident.get("priority", "UNKNOWN")).upper())
    status = html.escape(display_incident_status(status))
    reported = html.escape(display_incident_time(incident.get("timestamp")))
    latitude = float(incident.get("latitude", 0))
    longitude = float(incident.get("longitude", 0))
    is_report = bool(incident.get("is_report", False))
    report_details = incident.get("report_details")
    if pd.isna(report_details):
        report_details = ""
    report_details = str(report_details or "").strip()
    report_html = (
        '<div class="incident-hud-report"><strong>UNVERIFIED USER REPORT</strong>'
        + (
            f'<br>{html.escape(report_details)}'
            if report_details
            else ""
        )
        + "</div>"
        if is_report
        else ""
    )
    extra_block = (
        f'<div class="incident-hud-extra">{extra_html}</div>' if extra_html else ""
    )
    return (
        '<div class="incident-hud-content">'
        f'<div class="incident-hud-kicker">'
        f'{"ACTIVE INCIDENT" if is_active else "INCIDENT"}</div>'
        f'<div class="incident-hud-title">{incident_type}</div>'
        '<div class="incident-hud-grid">'
        f'<div class="incident-hud-field incident-hud-field-wide">'
        f'<span class="incident-hud-label">Area</span>'
        f'<span class="incident-hud-value">{address}</span></div>'
        f'<div class="incident-hud-field"><span class="incident-hud-label">'
        f'Reported</span><span class="incident-hud-value">{reported}</span></div>'
        f'<div class="incident-hud-field"><span class="incident-hud-label">'
        f'Incident ID</span><span class="incident-hud-value">{event_id}</span></div>'
        f'<div class="incident-hud-field incident-hud-field-wide">'
        f'<span class="incident-hud-label">Position</span>'
        f'<span class="incident-hud-value">{latitude:.5f}, {longitude:.5f}</span></div>'
        '</div>'
        f'<div class="incident-hud-state"><span>{priority}</span>'
        f'<span>{status}</span></div>'
        f'{report_html}{extra_block}</div>'
    )


def training_map_sync_html(training_cases, focus_event_id=None, focus_nonce=None):
    payload = json.dumps(training_cases, separators=(",", ":"), ensure_ascii=True)
    focus_payload = json.dumps(
        {"event_id": focus_event_id, "nonce": focus_nonce},
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return (
        "<script>window.frameElement.style.display='none';"
        "window.parent.sessionStorage.setItem("
        "'crime-radar-training-cases', "
        f"{json.dumps(payload)}"
        ");window.parent.sessionStorage.setItem("
        "'crime-radar-map-focus', "
        f"{json.dumps(focus_payload)}"
        ");window.parent.document.dispatchEvent("
        "new Event('crime-radar-map-update'));"
        "</script>"
    )


def display_patrol_status(status):
    return "AVAILABLE" if is_training_status(status) else str(status)


API_URL = os.getenv("CRIME_RADAR_API_URL", "http://127.0.0.1:8000").rstrip("/")
DASHBOARD_REFRESH_SECONDS = 2
CASE_ROTATION_SECONDS = 5


def get_json(path, params=None):
    response = requests.get(f"{API_URL}{path}", params=params, timeout=5)
    response.raise_for_status()
    return response.json()


ROUTING_SERVICE_URL = os.getenv(
    "CRIME_RADAR_ROUTING_URL", "https://router.project-osrm.org"
).rstrip("/")


@st.cache_data(ttl=300, max_entries=128, show_spinner=False)
def get_road_route(source_latitude, source_longitude, destination_latitude, destination_longitude):
    response = requests.get(
        f"{ROUTING_SERVICE_URL}/route/v1/driving/"
        f"{source_longitude},{source_latitude};"
        f"{destination_longitude},{destination_latitude}",
        params={"overview": "false", "steps": "false"},
        headers={"User-Agent": "C.R.I.M.E.-Radar-prototype"},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    routes = payload.get("routes", [])
    if payload.get("code") != "Ok" or not routes:
        raise ValueError("The routing service did not return a road route.")

    route = routes[0]
    return {
        "distance_km": route["distance"] / 1000,
        "duration_minutes": route["duration"] / 60,
    }


async def load_dashboard_data():
    incidents = await asyncio.to_thread(get_json, "/api/v1/active-incidents")
    units, sources, stations = await asyncio.gather(
        asyncio.to_thread(get_json, "/api/v1/patrol-units"),
        asyncio.to_thread(get_json, "/api/v1/sources"),
        asyncio.to_thread(get_json, "/api/v1/police-stations"),
    )
    return incidents, units, sources[0], stations


st.markdown(
    """
    <style>
    :root { color-scheme: dark; }
    .stApp { background: #07111d; color: #d7e4ef; }
    [data-testid="stMainBlockContainer"] { padding: .7rem 1rem 1rem; max-width: 100%; }
    [data-testid="stHeader"] { background: rgba(7, 17, 29, .94); }
    [data-testid="stStatusWidget"] { visibility: hidden; }
    [data-testid="stSidebar"] { background: #091522; border-right: 1px solid #193348; }
    [data-testid="stVerticalBlock"] { gap: .55rem; }
    [data-testid="stMetric"] { background: #0b1b2a; border: 1px solid #1b3a4d;
        border-radius: 8px; padding: 12px 14px; }
    .radar-kicker { color: #6e91a8; font-size: .75rem; letter-spacing: .16em;
        font-weight: 700; text-transform: uppercase; }
    .radar-title { color: #e3edf5; font-size: 1.4rem; font-weight: 750;
        letter-spacing: .025em; margin: 0; }
    .radar-source-note { color: #86a4b8; font-size: .72rem; margin: .25rem 0 0; }
    .radar-toolbar { background: #0a1927; border: 1px solid #1b3a4d;
        border-radius: 6px; padding: 8px 11px; color: #89a9bd; font-size: .76rem; }
    .radar-live-alert { background: #142736; border: 1px solid #1d5267;
        border-left: 3px solid #22bad1; color: #a6dcec; border-radius: 5px;
        padding: 8px 10px; font-size: .8rem; font-weight: 650; }
    .radar-no-feed { color: #ffc56c; font-size: .75rem; font-weight: 700;
        text-align: right; border: 1px solid #594729; background: #241e15;
        border-radius: 5px; padding: 6px 9px; }
    .radar-panel { background: #0a1927; border: 1px solid #1b3a4d;
        border-radius: 8px; padding: 14px; margin-bottom: 12px; }
    .radar-panel h3 { color: #aec6d6; font-size: .9rem; letter-spacing: .06em;
        text-transform: uppercase; margin: 0 0 10px; }
    .radar-alert { background: #35151c; color: #ff8991; border: 1px solid #852b36;
        border-radius: 6px; padding: 9px 11px; font-weight: 700; }
    .radar-zone { background: #0d2232; border-left: 3px solid #22bad1;
        border-radius: 4px; padding: 9px 10px; margin: 7px 0; }
    .radar-zone-critical { border-left-color: #ff4559; }
    .radar-zone small { color: #86a4b8; }
    .radar-cluster-card { background: #0b1b2a; border: 1px solid #1b3a4d;
        border-left: 3px solid #22bad1; border-radius: 4px; padding: 8px 9px;
        margin: 6px 0; color: #d7e4ef; font-size: .78rem; }
    .radar-cluster-card small { color: #86a4b8; }
    .radar-cluster-count { float: right; color: #45c6d8; font-size: 1rem;
        font-weight: 800; }
    div[data-testid="stVerticalBlockBorderWrapper"]:has(.incident-hud-content) {
        background: linear-gradient(145deg, #0c2235, #081523 75%);
        border: 1px solid #287f96; border-radius: 6px; padding: 10px 12px;
        box-shadow: inset 0 1px 0 rgba(89, 214, 232, .12),
            0 0 16px rgba(20, 190, 220, .1);
    }
    .incident-hud-kicker { color: #59d6e8; font-size: .65rem; font-weight: 750;
        letter-spacing: .14em; text-transform: uppercase; }
    .incident-hud-title { color: #effaff; font-size: .98rem; font-weight: 750;
        line-height: 1.25; margin: 4px 0 9px; overflow-wrap: anywhere; }
    .incident-hud-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
    .incident-hud-field { background: #0b2437; border-left: 2px solid #197b91;
        min-width: 0; padding: 6px 8px; }
    .incident-hud-field-wide { grid-column: 1 / -1; }
    .incident-hud-label { color: #70aabc; display: block; font-size: .58rem;
        font-weight: 750; letter-spacing: .1em; text-transform: uppercase; }
    .incident-hud-value { color: #e0f1f5; display: block; font-size: .76rem;
        line-height: 1.4; margin-top: 3px; overflow-wrap: anywhere; }
    .incident-hud-state { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
    .incident-hud-state span { background: #102d40; border: 1px solid #22677c;
        border-radius: 3px; color: #89e5ef; font-size: .59rem; font-weight: 750;
        letter-spacing: .07em; padding: 3px 6px; }
    .incident-hud-report, .incident-hud-extra { border-top: 1px solid #174054;
        color: #a8cbd5; font-size: .72rem; line-height: 1.5; margin-top: 9px;
        padding-top: 8px; overflow-wrap: anywhere; }
    div[data-testid="stVerticalBlockBorderWrapper"]:has(.incident-hud-content)
        [data-testid="stButton"] button {
        background: #0c2a3e; border: 1px solid #287f96; color: #b9edf3;
    }
    div[data-testid="stVerticalBlockBorderWrapper"]:has(.incident-hud-content)
        [data-testid="stButton"] button:hover {
        border-color: #59d6e8; color: #effaff;
        box-shadow: 0 0 12px rgba(20, 190, 220, .2);
    }
    .radar-status { color: #62d6a5; font-size: .8rem; font-weight: 700; }
    div[data-testid="stDataFrame"] { border: 1px solid #1b3a4d; border-radius: 8px; }
    div[data-testid="stMultiSelect"] [data-baseweb="tag"] {
        background: linear-gradient(135deg, #124458, #126878);
        border: 1px solid #35c9dc;
        border-radius: 999px;
        box-shadow: 0 0 10px rgba(53, 201, 220, .16);
        animation: station-chip-in .2s ease-out;
        transition: box-shadow .18s ease, transform .18s ease;
    }
    div[data-testid="stMultiSelect"] [data-baseweb="tag"]:hover {
        box-shadow: 0 0 15px rgba(53, 201, 220, .38);
        transform: translateY(-1px);
    }
    div[data-testid="stMultiSelect"]:focus-within [data-baseweb="select"] > div {
        border-color: #35c9dc;
        box-shadow: 0 0 0 1px #35c9dc, 0 0 14px rgba(53, 201, 220, .2);
    }
    @keyframes station-chip-in {
        from { opacity: .45; transform: scale(.92); }
        to { opacity: 1; transform: scale(1); }
    }
    iframe[title="st.iframe"][height="700"] {
        opacity: 1 !important;
        filter: none !important;
        transition: none !important;
        border: 1px solid #1b3a4d;
        border-radius: 8px;
        box-shadow: 0 0 22px rgba(8, 184, 211, .08);
    }
    div[data-testid="element-container"]:has(> iframe[title="st.iframe"][height="700"]) {
        opacity: 1 !important;
        transition: none !important;
    }
    div[data-testid="stHorizontalBlock"]:has(iframe[title="st.iframe"][height="700"])
        > div[data-testid="column"]:first-child {
        position: relative;
        min-width: 0;
        padding: 10px 12px;
        background: #091522;
        border: 1px solid #1b3a4d;
        border-radius: 6px;
        box-shadow: inset 0 1px 0 rgba(104, 202, 223, .08);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

header_left, header_right = st.columns([4, 1])
with header_left:
    st.markdown('<p class="radar-title">CRIME HOTSPOT MAPPING — TAMIL NADU</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="radar-source-note">C.R.I.M.E. RADAR · COORDINATED RESPONSE & INCIDENT MAPPING ENGINE</p>',
        unsafe_allow_html=True,
    )
with header_right:
    st.markdown(
        f'<p class="radar-no-feed">● NO LIVE AGENCY FEED<br>'
        f'<span style="font-weight:400">{time.strftime("%H:%M")} LOCAL · TN ONLY</span></p>',
        unsafe_allow_html=True,
    )

st.divider()

@st.fragment(run_every=DASHBOARD_REFRESH_SECONDS)
def render_live_dashboard():
    alert_confirmation = st.session_state.pop("station_alert_confirmation", None)
    if alert_confirmation:
        st.toast(alert_confirmation, icon="📡")

    try:
        incidents_data, units_data, source_status, police_stations = asyncio.run(
            load_dashboard_data()
        )
        data_error = None
    except (requests.RequestException, ValueError, KeyError) as exc:
        incidents_data, units_data, police_stations = [], [], []
        data_error = str(exc)

    if data_error:
        st.error(f"Telemetry service unavailable: {data_error}")
    else:
        left_panel, map_panel, right_panel = st.columns([1.3, 3.2, 1.5], gap="small")
        with left_panel:
            st.markdown('<div class="radar-panel"><h3>Activity Snapshot</h3></div>', unsafe_allow_html=True)
            show_demo_data = st.toggle(
                "Show incidents",
                value=True,
                help="Display incidents and available patrol markers.",
            )
        incoming_df = pd.DataFrame(incidents_data)
        if not incoming_df.empty:
            incoming_df["is_demo"] = incoming_df["status"].map(is_training_status)
            incoming_df["is_report"] = (
                incoming_df["status"].astype("string").str.upper() == "PENDING_REVIEW"
            )
            agency_df = incoming_df[
                ~incoming_df["is_demo"] & ~incoming_df["is_report"]
            ].copy()
            report_df = incoming_df[incoming_df["is_report"]].copy()
            persisted_demo_df = incoming_df[incoming_df["is_demo"]].copy()
        else:
            agency_df = incoming_df
            report_df = incoming_df
            persisted_demo_df = incoming_df
        demo_df = (
            persisted_demo_df
            if show_demo_data
            else pd.DataFrame()
        )
        df = pd.concat([agency_df, report_df, demo_df], ignore_index=True)
        if not df.empty:
            training_rows = df["is_demo"].astype(bool)
            df.loc[training_rows, "event_id"] = df.loc[training_rows, "event_id"].map(
                lambda event_id: display_event_id(event_id, True)
            )
            df.loc[training_rows, "incident_type"] = df.loc[
                training_rows, "incident_type"
            ].map(lambda incident_type: display_incident_type(incident_type, True))
            df.loc[training_rows, "status"] = df.loc[training_rows, "status"].map(
                display_incident_status
            )
            df.loc[training_rows, "address"] = df.loc[training_rows, "address"].map(
                lambda address: display_incident_address(address, True)
            )
            df["_display_timestamp"] = pd.to_datetime(
                df["timestamp"], errors="coerce", utc=True
            )
            builtin_training_rows = df["event_id"].astype(str).str.startswith("TRAIN-TN-")
            df.loc[builtin_training_rows, "_display_timestamp"] = pd.Timestamp(
                "1970-01-01", tz="UTC"
            )
            df = (
                df.sort_values(
                    "_display_timestamp",
                    ascending=False,
                    na_position="last",
                    kind="stable",
                )
                .drop(columns="_display_timestamp")
                .reset_index(drop=True)
            )
        incident_ids = df["event_id"].astype(str).tolist() if not df.empty else []
        incident_action_queue = st.session_state.get("incident_action_queue")
        new_report_ids = []
        if incident_action_queue is None:
            incident_action_queue = incident_ids.copy()
            st.session_state["incident_action_queue"] = incident_action_queue
            st.session_state["known_action_incident_ids"] = set(incident_ids)
            st.session_state["released_action_incident_ids"] = set(
                incident_action_queue[:1]
            )
        else:
            known_action_incident_ids = st.session_state.setdefault(
                "known_action_incident_ids", set(incident_action_queue)
            )
            released_action_incident_ids = st.session_state.setdefault(
                "released_action_incident_ids", set(incident_action_queue[:1])
            )
            new_incident_ids = [
                event_id for event_id in incident_ids
                if event_id not in known_action_incident_ids
            ]
            new_report_ids = [
                str(incident["event_id"])
                for incident in df.loc[df["is_report"]].to_dict(orient="records")
                if str(incident["event_id"]) in new_incident_ids
            ]
            if incident_action_queue:
                incident_action_queue[1:1] = new_report_ids
            else:
                incident_action_queue.extend(new_report_ids)
            incident_action_queue.extend(
                event_id for event_id in new_incident_ids
                if event_id not in new_report_ids
            )
            known_action_incident_ids.update(new_incident_ids)
        live_incident_ids = set(incident_ids)
        incident_action_queue[:] = [
            event_id
            for event_id in incident_action_queue
            if event_id in live_incident_ids
        ]
        if not st.session_state.get("action_queue_prioritized_for_map"):
            report_ids = (
                set(df.loc[df["is_report"], "event_id"].astype(str))
                if not df.empty
                else set()
            )
            new_report_ids = set(new_report_ids)
            pending_ids = incident_action_queue[1:]
            incident_action_queue[1:] = (
                [event_id for event_id in pending_ids if event_id in new_report_ids]
                + [
                    event_id
                    for event_id in pending_ids
                    if event_id not in report_ids and event_id not in new_report_ids
                ]
                + [
                    event_id
                    for event_id in pending_ids
                    if event_id in report_ids and event_id not in new_report_ids
                ]
            )
            st.session_state["action_queue_prioritized_for_map"] = True
        released_action_incident_ids = st.session_state.setdefault(
            "released_action_incident_ids", set(incident_action_queue[:1])
        )
        previous_incidents = st.session_state.get("previous_action_incidents")
        if previous_incidents is None:
            queued_ids = set(incident_action_queue)
            known_ids = st.session_state.get("known_action_incident_ids", set())
            sent_ids = {
                item["incident_id"]
                for item in st.session_state.get("station_alert_outbox", [])
            }
            previous_incidents = [
                incident
                for incident in df.to_dict(orient="records")
                if str(incident["event_id"]) in known_ids
                and str(incident["event_id"]) not in queued_ids
                and str(incident["event_id"]) not in sent_ids
            ]
            st.session_state["previous_action_incidents"] = previous_incidents
        now = time.monotonic()
        last_rotation_at = st.session_state.setdefault(
            "last_auto_case_rotation_at", now
        )
        if now - last_rotation_at >= CASE_ROTATION_SECONDS:
            st.session_state["last_auto_case_rotation_at"] = now
            if incident_action_queue:
                previous_incident_id = incident_action_queue.pop(0)
                previous_matches = df.loc[
                    df["event_id"].astype(str).eq(previous_incident_id)
                ]
                previous_incidents = st.session_state.setdefault(
                    "previous_action_incidents", []
                )
                if not previous_matches.empty and not any(
                    str(item["event_id"]) == previous_incident_id
                    for item in previous_incidents
                ):
                    previous_incidents.append(
                        previous_matches.iloc[0].to_dict()
                    )
                    del previous_incidents[:-50]
            if incident_action_queue:
                released_action_incident_ids.add(incident_action_queue[0])

        current_critical = (
            df.loc[df["priority"].astype("string").str.upper().eq("CRITICAL")]
            .to_dict(orient="records")
            if not df.empty
            else []
        )
        current_critical_ids = {str(incident["event_id"]) for incident in current_critical}
        alert_case_queue = st.session_state.get("critical_alert_case_queue")
        observed_critical_ids = st.session_state.get("observed_critical_incident_ids")
        if alert_case_queue is None or observed_critical_ids is None:
            alert_case_queue = [
                str(incident["event_id"]) for incident in current_critical
            ]
            observed_critical_ids = current_critical_ids.copy()
        else:
            alert_case_queue = [
                event_id
                for event_id in alert_case_queue
                if event_id in current_critical_ids
            ]
            newly_observed_critical_ids = [
                str(incident["event_id"])
                for incident in current_critical
                if str(incident["event_id"]) not in observed_critical_ids
            ]
            alert_case_queue.extend(newly_observed_critical_ids)
            observed_critical_ids.update(current_critical_ids)
        st.session_state["critical_alert_case_queue"] = alert_case_queue
        st.session_state["observed_critical_incident_ids"] = observed_critical_ids

        simulated_units = [
            unit
            for unit in units_data
            if show_demo_data or not is_training_status(unit["status"])
        ]

        demo_critical_incidents = (
            df.loc[df["is_demo"] & df["priority"].astype("string").str.upper().eq("CRITICAL")]
            .to_dict(orient="records")
            if not df.empty
            else []
        )
        unit_by_id = {str(unit["unit_id"]): unit for unit in simulated_units}
        station_by_id = {
            station["station_id"]: station for station in police_stations
        }
        active_incident_ids = {
            str(incident["event_id"]) for incident in demo_critical_incidents
        }
        existing_manual_assignments = st.session_state.get(
            "manual_training_dispatch_assignments", {}
        )
        manual_assignments = {}
        used_unit_ids = set()
        for event_id, assignment in existing_manual_assignments.items():
            event_id = str(event_id)
            station_id = str(assignment.get("station_id", ""))
            unit_id = str(assignment.get("unit_id", ""))
            if (
                event_id in active_incident_ids
                and station_id in station_by_id
                and unit_id in unit_by_id
                and unit_id not in used_unit_ids
            ):
                manual_assignments[event_id] = {
                    "station_id": station_id,
                    "unit_id": unit_id,
                }
                used_unit_ids.add(unit_id)
        st.session_state["manual_training_dispatch_assignments"] = manual_assignments

        existing_assignments = st.session_state.get("demo_dispatch_assignments", {})
        assignments = {
            event_id: assignment["unit_id"]
            for event_id, assignment in manual_assignments.items()
        }
        for event_id, unit_id in existing_assignments.items():
            event_id = str(event_id)
            unit_id = str(unit_id)
            if (
                event_id in active_incident_ids
                and event_id not in assignments
                and unit_id in unit_by_id
                and unit_id not in used_unit_ids
            ):
                assignments[event_id] = unit_id
                used_unit_ids.add(unit_id)

        unassigned_incidents = [
            incident
            for incident in demo_critical_incidents
            if str(incident["event_id"]) not in assignments
        ]
        available_units = [
            unit for unit_id, unit in unit_by_id.items() if unit_id not in used_unit_ids
        ]
        station_by_unit_id = {
            station["unit_id"]: station for station in police_stations
        }

        def station_distance(unit, incident):
            station = station_by_unit_id.get(str(unit["unit_id"]), unit)
            return haversine(
                station["longitude"],
                station["latitude"],
                incident["longitude"],
                incident["latitude"],
            )

        candidate_assignments = sorted(
            (
                station_distance(unit, incident),
                str(incident["event_id"]),
                str(unit["unit_id"]),
            )
            for incident in unassigned_incidents
            for unit in available_units
        )
        for _, event_id, unit_id in candidate_assignments:
            if event_id not in assignments and unit_id not in used_unit_ids:
                assignments[event_id] = unit_id
                used_unit_ids.add(unit_id)

        st.session_state["demo_dispatch_assignments"] = assignments
        dispatch_by_incident = {}
        for incident in current_critical:
            event_id = str(incident["event_id"])
            unit_id = assignments.get(event_id)
            if unit_id is not None:
                unit = unit_by_id[unit_id]
                manual_assignment = manual_assignments.get(event_id)
                station = (
                    station_by_id[manual_assignment["station_id"]]
                    if manual_assignment
                    else next(
                        (
                            item
                            for item in police_stations
                            if item["unit_id"] == unit_id
                        ),
                        None,
                    )
                )
                if station is None:
                    station, _ = nearest_training_station(
                        incident["latitude"], incident["longitude"], police_stations
                    )
                dispatch_by_incident[event_id] = {
                    "unit": unit,
                    "distance_km": haversine(
                        unit["longitude"],
                        unit["latitude"],
                        incident["longitude"],
                        incident["latitude"],
                    ),
                    "station": station,
                    "station_distance_km": (
                        haversine(
                            station["longitude"],
                            station["latitude"],
                            incident["longitude"],
                            incident["latitude"],
                        )
                        if station is not None
                        else None
                    ),
                    "nearby_stations": nearest_training_stations(
                        incident["latitude"], incident["longitude"], police_stations, max_count=1,
                        exclude_station_id=station["station_id"] if station else None
                    ),
                    "manual": event_id in manual_assignments,
                }
            else:
                station, station_distance_km = nearest_training_station(
                    incident["latitude"], incident["longitude"], police_stations
                )
                nearby_stations = nearest_training_stations(
                    incident["latitude"], incident["longitude"], police_stations, max_count=1,
                    exclude_station_id=station["station_id"] if station else None
                )
                dispatch_by_incident[event_id] = {
                    "unit": None,
                    "distance_km": None,
                    "station": station,
                    "station_distance_km": station_distance_km,
                    "nearby_stations": nearby_stations,
                    "manual": False,
                }
            if event_id in dispatch_by_incident:
                dispatch_by_incident[event_id]["manual"] = event_id in manual_assignments

        incident_types = sorted(df["incident_type"].dropna().unique()) if not df.empty else []
        priorities = sorted(df["priority"].dropna().unique()) if not df.empty else []
        incident_statuses = (
            sorted(df["status"].dropna().astype(str).unique()) if not df.empty else []
        )
        if not df.empty:
            available_timestamps = pd.to_datetime(
                df["timestamp"], errors="coerce", utc=True
            ).dropna()
        else:
            available_timestamps = pd.Series(dtype="datetime64[ns, UTC]")
        if available_timestamps.empty:
            date_min = date_max = datetime.now(timezone.utc).date()
        else:
            date_min = available_timestamps.min().date()
            date_max = available_timestamps.max().date()
        area_options = sorted(
            {
                str(address).split(",", 1)[0].strip()
                for address in df["address"].dropna()
            }
        ) if not df.empty else []

        with left_panel:
            critical_count = int((df["priority"].str.upper() == "CRITICAL").sum()) if not df.empty else 0
            real_count = (
                int((~df["is_demo"] & ~df["is_report"]).sum()) if not df.empty else 0
            )
            report_count = int(df["is_report"].sum()) if not df.empty else 0
            demo_critical_count = (
                int(((df["priority"].str.upper() == "CRITICAL") & df["is_demo"]).sum())
                if not df.empty
                else 0
            )
            real_critical_count = critical_count - demo_critical_count
            report_timestamps = (
                pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
                if not df.empty
                else pd.Series(dtype="datetime64[ns, UTC]")
            )
            now_utc = pd.Timestamp.now(tz="UTC")
            recent_24h = report_timestamps.ge(now_utc - pd.Timedelta(hours=24))
            recent_count = int(recent_24h.sum())
            st.metric("Incidents", len(df))
            st.metric("Critical", int((df["priority"].str.upper() == "CRITICAL").sum()) if not df.empty else 0)
            latest_types = (
                df.loc[recent_24h, "incident_type"].value_counts()
                if not df.empty and recent_count
                else pd.Series(dtype=int)
            )
            peak_type = str(latest_types.index[0]) if not latest_types.empty else "Awaiting incidents"
            st.markdown(
                f'<div class="radar-zone"><small>Top crime type · last 24h</small><br>'
                f'<strong>{html.escape(peak_type)}</strong></div>',
                unsafe_allow_html=True,
            )
            latest_incident = df.iloc[0] if not df.empty else None
            latest_incident_type = (
                display_incident_type(
                    latest_incident["incident_type"],
                    bool(latest_incident["is_demo"]),
                )
                if latest_incident is not None
                else "Awaiting telemetry"
            )
            st.markdown(
                f"**Latest incident:** "
                f"{html.escape(latest_incident_type)}"
            )
            st.caption(
                f"Agency: {real_count} · Submitted: {report_count} · "
                f"Critical: {critical_count} · Auto-refresh active"
            )

        with left_panel:
            st.markdown(
                '<div class="radar-panel"><h3>Filters & Layers</h3></div>', unsafe_allow_html=True)
            address_search = st.text_input(
                "Search address",
                placeholder="Search address or area",
                key="incident-address-search",
            )
            selected_date_range = st.date_input(
                "Date range",
                value=(date_min, date_max),
                min_value=date_min,
                max_value=date_max,
                key="incident-date-range",
            )
            selected_time_window = st.selectbox(
                "Time",
                ["All day", "18:00-06:00", "06:00-12:00", "12:00-18:00"],
                key="incident-time-window",
            )
            selected_area = st.selectbox(
                "Location",
                ["All areas", *area_options],
                key="incident-area-filter",
            )
            selected_type = st.selectbox("Crime type", ["All types", *incident_types])
            selected_priority = st.selectbox("Priority", ["All priorities", *priorities])
            selected_status = st.selectbox("Status", ["All statuses", *incident_statuses])
            show_heatmap = st.checkbox("Incident heat layer", value=True)
            show_patrol_units = st.checkbox("Show patrol telemetry", value=True)

        filtered_df = df.copy()
        if len(selected_date_range) == 2:
            range_start = pd.Timestamp(selected_date_range[0], tz="UTC")
            range_end = pd.Timestamp(
                selected_date_range[1] + timedelta(days=1), tz="UTC"
            )
            incident_times = pd.to_datetime(
                filtered_df["timestamp"], errors="coerce", utc=True
            )
            filtered_df = filtered_df.loc[
                incident_times.ge(range_start) & incident_times.lt(range_end)
            ]
        incident_times = pd.to_datetime(
            filtered_df["timestamp"], errors="coerce", utc=True
        )
        incident_hours = incident_times.dt.hour
        if selected_time_window == "18:00-06:00":
            filtered_df = filtered_df.loc[
                incident_hours.ge(18) | incident_hours.lt(6)
            ]
        elif selected_time_window == "06:00-12:00":
            filtered_df = filtered_df.loc[
                incident_hours.ge(6) & incident_hours.lt(12)
            ]
        elif selected_time_window == "12:00-18:00":
            filtered_df = filtered_df.loc[
                incident_hours.ge(12) & incident_hours.lt(18)
            ]
        if selected_area != "All areas":
            incident_areas = (
                filtered_df["address"]
                .astype("string")
                .str.split(",", n=1)
                .str[0]
                .str.strip()
            )
            filtered_df = filtered_df.loc[incident_areas.eq(selected_area)]
        if selected_type != "All types":
            filtered_df = filtered_df[filtered_df["incident_type"] == selected_type]
        if selected_priority != "All priorities":
            filtered_df = filtered_df[filtered_df["priority"] == selected_priority]
        if selected_status != "All statuses":
            filtered_df = filtered_df[filtered_df["status"].astype(str) == selected_status]
        if address_search.strip():
            filtered_df = filtered_df[
                filtered_df["address"]
                .astype("string")
                .str.contains(address_search.strip(), case=False, regex=False, na=False)
            ]

        current_alert_id = alert_case_queue[0] if alert_case_queue else None
        current_alert_incident = (
            next(
                (
                    incident
                    for incident in current_critical
                    if str(incident["event_id"]) == current_alert_id
                ),
                None,
            )
            if current_alert_id is not None
            else None
        )
        focused_event_id = st.session_state.get("map_focus_event_id")
        map_df = filtered_df.copy()
        if not map_df.empty:
            staged_incidents = map_df["is_demo"].astype(bool)
            released_staged_incidents = map_df["event_id"].astype(str).isin(
                released_action_incident_ids
            )
            map_df = map_df.loc[
                ~staged_incidents | released_staged_incidents
            ].copy()
        active_action_incident_id = (
            str(incident_action_queue[0]) if incident_action_queue else None
        )
        map_focus_ids = {
            str(event_id)
            for event_id in (focused_event_id, active_action_incident_id)
            if event_id is not None
        }
        if not df.empty:
            visible_report_ids = set(
                map_df.loc[map_df["is_report"], "event_id"].astype(str)
            )
            new_report_rows = df.loc[
                df["is_report"] & ~df["event_id"].astype(str).isin(visible_report_ids)
            ]
            if not new_report_rows.empty:
                map_df = pd.concat([map_df, new_report_rows], ignore_index=True)
        for event_id in map_focus_ids:
            if not df.empty and not map_df["event_id"].astype(str).eq(event_id).any():
                focus_rows = df[df["event_id"].astype(str) == event_id]
                if not focus_rows.empty and (
                    not bool(focus_rows.iloc[0]["is_demo"])
                    or event_id in released_action_incident_ids
                ):
                    map_df = pd.concat([map_df, focus_rows], ignore_index=True)
        training_map_events = (
            map_df.to_dict(orient="records") if not map_df.empty else []
        )
        training_map_payload = []
        for incident in training_map_events:
            event_id = str(incident["event_id"])
            is_demo = bool(incident["is_demo"])
            incident_type = html.escape(
                display_incident_type(incident["incident_type"], is_demo)
            )
            dispatch = dispatch_by_incident.get(event_id)
            dispatch_detail = ""
            if dispatch and dispatch["station"] is not None:
                dispatch_detail = (
                    f"<br><b>NEAREST STATION:</b> "
                    f"{html.escape(dispatch['station']['name'])}"
                    f" · {dispatch['station_distance_km']:.2f} km away"
                )
            if dispatch and dispatch.get("nearby_stations"):
                nearby_html = "<br><b>NEARBY STATIONS:</b>"
                for nearby_station, dist_km in dispatch["nearby_stations"][:1]:
                    if nearby_station["station_id"] != dispatch.get("station", {}).get("station_id"):
                        nearby_html += (
                            f"<br>{html.escape(nearby_station['name'])} · {dist_km:.2f} km"
                        )
                dispatch_detail += nearby_html
            if dispatch:
                if dispatch["unit"] is not None:
                    dispatch_detail += (
                        f"<br><b>{'MANUAL ' if dispatch['manual'] else ''}"
                        f"PATROL ASSIGNED:</b> "
                        f"{html.escape(str(dispatch['unit']['unit_id']).replace('SIM-TN-UNIT-', 'UNIT-'))}"
                        f" · {dispatch['distance_km']:.2f} km away"
                    )
            training_map_payload.append(
                {
                    "event_id": event_id,
                    "incident_type": incident_type,
                    "latitude": float(incident["latitude"]),
                    "longitude": float(incident["longitude"]),
                    "priority": str(incident["priority"]).upper(),
                    "is_demo": is_demo,
                    "popup_html": incident_hud_html(
                        incident,
                        extra_html=dispatch_detail,
                        is_active=event_id == active_action_incident_id,
                    ),
                }
            )
        render_map_df = map_df.iloc[0:0].copy()
        critical_df = filtered_df[filtered_df["priority"].str.upper() == "CRITICAL"] if not filtered_df.empty else filtered_df
        target = critical_df.iloc[0] if not critical_df.empty else (filtered_df.iloc[0] if not filtered_df.empty else None)

        with left_panel:
            if real_critical_count:
                st.markdown(
                    f'<div class="radar-alert">AGENCY ALERT: {real_critical_count} critical '
                    f'incident{"s" if real_critical_count != 1 else ""}</div>',
                    unsafe_allow_html=True,
                )
            if demo_critical_count:
                st.markdown(
                    f'<div class="radar-live-alert">CRITICAL REPORTS: {demo_critical_count} '
                    f'critical event{"s" if demo_critical_count != 1 else ""}</div>',
                    unsafe_allow_html=True,
                )
            st.markdown('<div class="radar-panel"><h3>Map Legend</h3><small>State outline · Tamil Nadu jurisdiction<br>'
                        'Heat glow · incident density<br>'
                        'Red dot / cluster · incidents<br>'
                        'Blue PD badge · police station<br>'
                        'Blue marker · received patrol asset</small></div>', unsafe_allow_html=True)
        with map_panel:
            toolbar_city, toolbar_scope = st.columns([2, 1])
            with toolbar_city:
                city_centers = {
                    "Tamil Nadu": [10.8, 78.2],
                    "Chennai": [13.0827, 80.2707],
                    "Coimbatore": [11.0168, 76.9558],
                    "Madurai": [9.9252, 78.1198],
                    "Salem": [11.6643, 78.1460],
                    "Tiruchirappalli": [10.7905, 78.7047],
                    "Tirunelveli": [8.7139, 77.7567],
                    "Erode": [11.3410, 77.7172],
                    "Vellore": [12.9165, 79.1325],
                    "Tiruppur": [11.1085, 77.3411],
                    "Thanjavur": [10.7870, 79.1378],
                    "Cuddalore": [11.7480, 79.7714],
                    "Thoothukudi": [8.7642, 78.1348],
                    "Nagercoil": [8.1833, 77.4119],
                    "Dindigul": [10.3673, 77.9803],
                    "Hosur": [12.7409, 77.8253],
                    "Kanchipuram": [12.8342, 79.7036],
                    "Karur": [10.9601, 78.0766],
                    "Ramanathapuram": [9.3639, 78.8395],
                    "Pudukkottai": [10.3833, 78.8001],
                }
                map_city = st.selectbox(
                    "Map focus", list(city_centers), index=1,
                    label_visibility="collapsed", key="map-city-focus-v2"
                )
            with toolbar_scope:
                status_text = (
                    f"TAMIL NADU · {report_count} USER REPORTS"
                    if show_demo_data
                    else "TAMIL NADU · AGENCY DATA"
                )
                st.markdown(
                    f'<p class="radar-no-feed">{status_text}</p>',
                    unsafe_allow_html=True,
                )
            if real_critical_count:
                st.markdown(
                    f'<div class="radar-alert">⚠ AGENCY ALERT: {real_critical_count} critical '
                    f'incident{"s" if real_critical_count != 1 else ""} in received telemetry</div>',
                    unsafe_allow_html=True,
                )
            elif demo_critical_count:
                st.markdown(
                    f'<div class="radar-live-alert">⚠ '
                    f'{demo_critical_count} critical events</div>',
                    unsafe_allow_html=True,
                )
            elif not filtered_df.empty:
                st.markdown(
                    f'<div class="radar-live-alert">● {len(filtered_df)} matching report(s) · '
                    'See agency feed status above</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div class="radar-live-alert">● NO MATCHING INCIDENT REPORTS · '
                    'Awaiting authorized agency reports</div>',
                    unsafe_allow_html=True,
                )

            boundary_coordinates = [
                point
                for polygon in TAMIL_NADU_FEATURE["geometry"]["coordinates"]
                for point in polygon[0]
            ]
            south = min(point[1] for point in boundary_coordinates)
            west = min(point[0] for point in boundary_coordinates)
            north = max(point[1] for point in boundary_coordinates)
            east = max(point[0] for point in boundary_coordinates)
            radar_map = folium.Map(
                location=city_centers[map_city],
                zoom_start=6 if map_city == "Tamil Nadu" else 13,
                min_zoom=6,
                tiles=None,
                control_scale=True,
                max_bounds=True,
                min_lat=floor(south - 0.2),
                max_lat=ceil(north + 0.2),
                min_lon=floor(west - 0.2),
                max_lon=ceil(east + 0.2),
            )
            folium.TileLayer(
                tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
                "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
                attr="Tiles © Esri — Sources: Esri, HERE, Garmin, USGS, "
                "OpenStreetMap contributors, and the GIS User Community",
                name="Dark map",
                max_zoom=16,
                max_native_zoom=16,
                show=True,
            ).add_to(radar_map)
            folium.TileLayer(
                tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
                "Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
                attr="Labels © Esri",
                name="Dark map labels",
                max_zoom=16,
                max_native_zoom=16,
                overlay=True,
                control=False,
                show=True,
            ).add_to(radar_map)
            TacticalTileStyle().add_to(radar_map.get_root())
            outside_mask = {
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
                        *[
                            polygon[0]
                            for polygon in TAMIL_NADU_FEATURE["geometry"]["coordinates"]
                        ],
                    ],
                },
            }
            folium.GeoJson(
                outside_mask,
                name="Outside Tamil Nadu",
                style_function=lambda _: {
                    "color": "#07111d",
                    "weight": 0,
                    "fillColor": "#07111d",
                    "fillOpacity": 1,
                    "fillRule": "evenodd",
                },
            ).add_to(radar_map)
            heat_points = [
                [
                    float(incident["latitude"]),
                    float(incident["longitude"]),
                    1.8
                    if str(incident["priority"]).upper() == "CRITICAL"
                    else 1.0,
                ]
                for incident in map_df.to_dict(orient="records")
            ]
            if heat_points and show_heatmap:
                HeatMap(
                    heat_points,
                    name="Incident intensity",
                    min_opacity=0.28,
                    max_zoom=13,
                    radius=30,
                    blur=26,
                    gradient={
                        0.18: "#174d9b",
                        0.38: "#19b8d1",
                        0.6: "#e4e34f",
                        0.78: "#ff913b",
                        1.0: "#ff3e43",
                    },
                    control=False,
                    show=True,
                ).add_to(radar_map)
            folium.GeoJson(
                TAMIL_NADU_FEATURE,
                name="Tamil Nadu boundary",
                style_function=lambda _: {
                    "color": "#f6c453",
                    "weight": 2.5,
                    "fillColor": "#063d4c",
                    "fillOpacity": 0.16,
                },
            ).add_to(radar_map)
            station_reference_id = (
                st.session_state.get("route-planner-incident")
                or st.session_state.get("map_focus_event_id")
                or active_action_incident_id
            )
            station_reference_rows = (
                df.loc[
                    df["event_id"].astype(str).eq(str(station_reference_id))
                ]
                if station_reference_id is not None and not df.empty
                else pd.DataFrame()
            )
            station_reference = (
                station_reference_rows.iloc[0].to_dict()
                if not station_reference_rows.empty
                else None
            )
            if station_reference is None:
                station_reference = next(
                    (
                        incident
                        for incident in st.session_state.get(
                            "previous_action_incidents", []
                        )
                        if str(incident["event_id"]) == str(station_reference_id)
                    ),
                    None,
                )
            incident_area_station_ids = set()
            route_plan_specs = []
            route_station_by_id = {}
            road_routes = {}
            route_errors = {}
            route_coordinates_shared = st.session_state.get(
                "route-sharing-consent", False
            )
            if station_reference is not None:
                nearby_incident_stations = nearest_training_stations(
                    float(station_reference["latitude"]),
                    float(station_reference["longitude"]),
                    police_stations,
                    max_distance_km=35,
                )
                incident_area_station_ids = {
                    station["station_id"] for station, _ in nearby_incident_stations
                }
                if nearby_incident_stations:
                    route_station_by_id = {
                        str(station["station_id"]): station
                        for station, _ in nearby_incident_stations
                    }
                    route_station_ids = list(route_station_by_id)
                    route_station_key = f"route-station-{station_reference_id}"
                    selected_route_station_id = str(
                        st.session_state.get(route_station_key, route_station_ids[0])
                    )
                    if selected_route_station_id not in route_station_by_id:
                        selected_route_station_id = route_station_ids[0]
                    route_plan_specs.append(
                        (selected_route_station_id, "#a7f06a", None)
                    )
                    comparison_station_ids = [
                        station_id
                        for station_id in route_station_ids
                        if station_id != selected_route_station_id
                    ]
                    if comparison_station_ids:
                        comparison_key = (
                            f"route-compare-{station_reference_id}-"
                            f"{selected_route_station_id}"
                        )
                        comparison_station_id = str(
                            st.session_state.get(
                                comparison_key, comparison_station_ids[0]
                            )
                        )
                        if comparison_station_id not in comparison_station_ids:
                            comparison_station_id = comparison_station_ids[0]
                        route_plan_specs.append(
                            (comparison_station_id, "#ffbf54", "8, 6")
                        )
                    if route_coordinates_shared:
                        for station_id, _, _ in route_plan_specs:
                            destination_station = route_station_by_id[station_id]
                            try:
                                road_routes[station_id] = get_road_route(
                                    float(destination_station["latitude"]),
                                    float(destination_station["longitude"]),
                                    float(station_reference["latitude"]),
                                    float(station_reference["longitude"]),
                                )
                            except (
                                requests.RequestException,
                                ValueError,
                                KeyError,
                                TypeError,
                            ):
                                route_errors[station_id] = (
                                    "Road distance and ETA are unavailable."
                                )
            for station in police_stations:
                city_center = city_centers[map_city]
                is_near_map_city = (
                    haversine(
                        city_center[1],
                        city_center[0],
                        station["longitude"],
                        station["latitude"],
                    )
                    <= 35
                )
                if (
                    not is_near_map_city
                    and station["station_id"] not in incident_area_station_ids
                ):
                    continue
                nearest_incident = min(
                    map_df.to_dict(orient="records"),
                    key=lambda incident: haversine(
                        station["longitude"],
                        station["latitude"],
                        incident["longitude"],
                        incident["latitude"],
                    ),
                    default=None,
                )
                station_details = (
                    f"<b>Area:</b> "
                    f"{html.escape(station['name'].removesuffix(' Station'))}<br>"
                    "Police station location"
                )
                if station_reference is not None:
                    station_distance = haversine(
                        station["longitude"],
                        station["latitude"],
                        float(station_reference["longitude"]),
                        float(station_reference["latitude"]),
                    )
                    station_details += (
                        f"<br><b>Distance from selected incident:</b> "
                        f"{station_distance:.2f} km"
                    )
                elif nearest_incident is not None:
                    nearest_distance = haversine(
                        station["longitude"],
                        station["latitude"],
                        nearest_incident["longitude"],
                        nearest_incident["latitude"],
                    )
                    station_details += (
                        f"<br><b>Nearest displayed incident:</b> "
                        f"{html.escape(display_incident_type(nearest_incident['incident_type'], bool(nearest_incident['is_demo'])))}"
                        f"<br><b>Distance:</b> {nearest_distance:.2f} km"
                    )
                folium.Marker(
                    location=[station["latitude"], station["longitude"]],
                    popup=(
                        f"<b>{html.escape(station['name'])}</b><br>"
                        f"{station_details}"
                    ),
                    tooltip=html.escape(station["name"]),
                    icon=folium.DivIcon(
                        html=(
                            '<div class="police-station-badge">PD</div>'
                        ),
                        icon_size=(32, 34),
                        icon_anchor=(16, 17),
                    ),
                ).add_to(radar_map)
            incident_cluster = MarkerCluster(
                name="Incident markers",
                icon_create_function=(
                    "function(cluster) {"
                    "return L.divIcon({"
                    "html: '<div class=\"incident-cluster-icon\" "
                    "title=\"Incident locations · click to zoom\">"
                    "<div class=\"incident-cluster-dot\"></div></div>',"
                    "className: '', iconSize: L.point(34, 34)"
                    "});}"
                ),
                options={
                    "maxClusterRadius": 42,
                    "disableClusteringAtZoom": 16,
                    "spiderfyOnMaxZoom": True,
                    "showCoverageOnHover": False,
                    "animate": False,
                    "animateAddingMarkers": False,
                },
            ).add_to(radar_map)
            IncidentClusterRegistry(incident_cluster).add_to(radar_map)
            if not render_map_df.empty:
                for _, incident in render_map_df.iterrows():
                    is_demo = bool(incident["is_demo"])
                    is_report = bool(incident["is_report"])
                    is_critical = str(incident["priority"]).upper() == "CRITICAL"
                    incident_location = [incident["latitude"], incident["longitude"]]
                    incident_name = html.escape(
                        display_incident_type(incident["incident_type"], is_demo)
                    )
                    incident_address = html.escape(
                        display_incident_address(incident["address"], is_demo)
                    )
                    incident_id = str(incident["event_id"])
                    dispatch = dispatch_by_incident.get(incident_id)
                    dispatch_detail = ""
                    if dispatch and dispatch["station"] is not None:
                        dispatch_detail = (
                            f"<br><b>NEAREST STATION:</b> "
                            f"{html.escape(dispatch['station']['name'])}"
                            f" · {dispatch['station_distance_km']:.2f} km away"
                        )
                    if dispatch and dispatch.get("nearby_stations"):
                        nearby_html = "<br><b>NEARBY STATIONS:</b>"
                        for nearby_station, dist_km in dispatch["nearby_stations"][:1]:
                            if nearby_station["station_id"] != dispatch.get("station", {}).get("station_id"):
                                nearby_html += (
                                    f"<br>{html.escape(nearby_station['name'])} · {dist_km:.2f} km"
                                )
                        dispatch_detail += nearby_html
                    if dispatch:
                        if dispatch["unit"] is not None:
                            dispatch_detail += (
                                f"<br><b>{'MANUAL ' if dispatch['manual'] else ''}"
                                f"PATROL ASSIGNED:</b> "
                                f"{html.escape(str(dispatch['unit']['unit_id']).replace('SIM-TN-UNIT-', 'UNIT-'))}"
                                f" · {dispatch['distance_km']:.2f} km away"
                            )
                    is_selected = str(
                        st.session_state.get("map_focus_event_id", "")
                    ) == incident_id
                    marker_classes = ["incident-map-marker"]
                    if is_critical:
                        marker_classes.append("critical")
                    if is_selected:
                        marker_classes.append("selected")
                    marker_size = 22 if is_selected else 18
                    marker_anchor = marker_size // 2
                    incident_marker = folium.Marker(
                        location=incident_location,
                        icon=folium.DivIcon(
                            html=(
                                f'<div class="{" ".join(marker_classes)}"></div>'
                            ),
                            icon_size=(marker_size, marker_size),
                            icon_anchor=(marker_anchor, marker_anchor),
                        ),
                        tooltip=(
                            incident_name
                        ),
                        popup=folium.Popup(
                            incident_hud_html(
                                incident,
                                extra_html=dispatch_detail,
                            ),
                            max_width=280,
                            show=False,
                        ),
                    ).add_to(incident_cluster)
                    IncidentMarkerRegistry(incident_id, incident_marker).add_to(
                        radar_map
                    )
            incident_targets = (
                filtered_df.loc[
                    ~filtered_df["is_demo"] & ~filtered_df["is_report"]
                ].to_dict(orient="records")
                if not filtered_df.empty
                else []
            )
            displayed_units = units_data + (
                [unit for unit in simulated_units if unit not in units_data]
                if show_demo_data
                else []
            )
            if show_patrol_units:
                for unit in displayed_units:
                    unit_location = [unit["latitude"], unit["longitude"]]
                    unit_status = str(unit["status"])
                    is_demo_unit = is_training_status(unit_status)
                    is_available = unit_status.strip().upper() == "AVAILABLE"
                    unit_popup = (
                        f"{html.escape(unit['unit_id'])}<br>"
                        f"Status: {html.escape(display_patrol_status(unit_status))}"
                    )
                    unit_tooltip = (
                        html.escape(str(unit["unit_id"]).replace("SIM-TN-UNIT-", "UNIT-"))
                    )
                    assigned_to = next(
                        (
                            event_id
                            for event_id, assigned_unit_id in assignments.items()
                            if assigned_unit_id == str(unit["unit_id"])
                        ),
                        None,
                    )
                    if is_available and not is_demo_unit and incident_targets:
                        assigned_incident = min(
                            incident_targets,
                            key=lambda incident: haversine(
                                unit["longitude"],
                                unit["latitude"],
                                incident["longitude"],
                                incident["latitude"],
                            ),
                        )
                        distance = haversine(
                            assigned_incident["longitude"],
                            assigned_incident["latitude"],
                            unit["longitude"],
                            unit["latitude"],
                        )
                        unit_popup += f"<br>Intercept distance: {distance:.2f} km"
                        unit_tooltip += f" · {distance:.1f} km"
                    elif is_demo_unit and assigned_to:
                        assigned_incident = next(
                            incident
                            for incident in demo_critical_incidents
                            if str(incident["event_id"]) == assigned_to
                        )
                        distance = haversine(
                            assigned_incident["longitude"],
                            assigned_incident["latitude"],
                            unit["longitude"],
                            unit["latitude"],
                        )
                        unit_popup += (
                            f"<br>ASSIGNED TO: {html.escape(display_event_id(assigned_to, True))}"
                            f"<br>Distance: {distance:.2f} km"
                        )
                        unit_tooltip += f" · ASSIGNED · {distance:.1f} km"
                    patrol_marker = folium.Marker(
                        location=unit_location,
                        popup=unit_popup,
                        tooltip=unit_tooltip,
                        icon=folium.Icon(color="blue", icon="shield", prefix="fa"),
                    ).add_to(radar_map)

            if show_patrol_units and displayed_units:
                st.caption(f"Displaying {len(displayed_units)} patrol marker(s).")
            elif show_patrol_units:
                st.caption("No recent patrol telemetry received.")
            components.html(
                training_map_sync_html(
                    training_map_payload,
                    st.session_state.get("map_focus_event_id"),
                    st.session_state.get("map_focus_nonce"),
                ),
                height=0,
                scrolling=False,
            )
            MapFocusWatcher(map_city).add_to(radar_map)

            map_signature = json.dumps(
                {
                    "map_mode": "incremental-training-v21",
                    "city": map_city,
                    "show_training": show_demo_data,
                    "show_patrol_units": show_patrol_units,
                    "route_incident": station_reference_id,
                    "route_coordinates_shared": route_coordinates_shared,
                    "heat_points": heat_points,
                    "route_stations": [
                        station_id
                        for station_id, _, _ in route_plan_specs
                    ],
                    "route_errors": route_errors,
                },
                sort_keys=True,
                default=str,
            )
            if st.session_state.get("tactical_map_signature") != map_signature:
                st.session_state["tactical_map_signature"] = map_signature
                st.session_state["tactical_map_html"] = radar_map.get_root().render()
            components.html(
                st.session_state["tactical_map_html"],
                height=700,
                scrolling=False,
            )
            st.caption(
                f"Boundary: [geoBoundaries India ADM1 · Tamil Nadu]"
                f"({source_status['boundary_url']}) · {source_status['boundary_license']}"
            )

        with right_panel:
            if not filtered_df.empty:
                area_frame = filtered_df.assign(
                    _activity_area=(
                        filtered_df["address"]
                        .astype("string")
                        .str.split(",", n=1)
                        .str[0]
                        .str.strip()
                    )
                )
                cluster_counts = (
                    area_frame.groupby(["_activity_area", "incident_type"])
                    .size()
                    .sort_values(ascending=False)
                    .head(3)
                )
                critical_rows = area_frame.loc[
                    area_frame["priority"].astype("string").str.upper().eq("CRITICAL")
                ]
                if not critical_rows.empty:
                    leading_critical = (
                        critical_rows.groupby(["_activity_area", "incident_type"])
                        .size()
                        .sort_values(ascending=False)
                    )
                    (critical_area, critical_type), critical_total = leading_critical.index[0], int(leading_critical.iloc[0])
                    st.markdown(
                        f'<div class="radar-alert">ALERT · '
                        f'{html.escape(str(critical_type))} · '
                        f'{html.escape(str(critical_area))} · {critical_total}</div>',
                        unsafe_allow_html=True,
                    )
                st.markdown('<div class="radar-panel"><h3>Filtered Clusters</h3></div>', unsafe_allow_html=True)
                for (activity_area, incident_type), count in cluster_counts.items():
                    st.markdown(
                        f'<div class="radar-cluster-card">'
                        f'<span class="radar-cluster-count">{int(count)}</span>'
                        f'<strong>{html.escape(str(activity_area))}</strong><br>'
                        f'<small>{html.escape(str(incident_type))}</small></div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No incidents match the current filters.")
            st.markdown('<div class="radar-panel"><h3>Incident Alerts</h3></div>', unsafe_allow_html=True)
            active_incident_id = (
                incident_action_queue[0] if incident_action_queue else None
            )
            active_matches = (
                df.loc[df["event_id"].astype(str).eq(active_incident_id)]
                if active_incident_id is not None and not df.empty
                else pd.DataFrame()
            )
            if not active_matches.empty:
                st.caption(
                    f"NEW CASE · AUTO-ADVANCING EVERY "
                    f"{CASE_ROTATION_SECONDS} SECONDS"
                )
                active_incident = active_matches.iloc[0]
                incident_type = str(active_incident["incident_type"])
                address = str(active_incident["address"])
                latitude = float(active_incident["latitude"])
                longitude = float(active_incident["longitude"])
                nearby_stations = nearest_training_stations(
                    latitude,
                    longitude,
                    police_stations,
                    max_distance_km=35,
                )
                with st.container(border=True):
                    st.markdown(
                        incident_hud_html(active_incident, is_active=True),
                        unsafe_allow_html=True,
                    )
                    st.button(
                        "Show location on map",
                        key=f"show-incident-location-{active_incident_id}",
                        use_container_width=True,
                        on_click=focus_incident_on_map,
                        args=(active_incident_id,),
                    )
                    if not nearby_stations:
                        st.caption("Nearby police stations unavailable")
                        st.button(
                            "Send alert message",
                            key=f"send-incident-alert-{active_incident_id}",
                            use_container_width=True,
                            disabled=True,
                        )
                    else:
                        st.caption("Nearby police stations:")
                        for nearby_station, station_distance in nearby_stations:
                            st.markdown(
                                f"- {html.escape(str(nearby_station['name']))} · "
                                f"{station_distance:.1f} km"
                            )
                        station_choices = {
                            str(station["station_id"]): (station, station_distance)
                            for station, station_distance in nearby_stations
                        }
                        selected_station_id = st.selectbox(
                            "Send alert to",
                            list(station_choices),
                            format_func=lambda station_id: (
                                f"{station_choices[station_id][0]['name']} · "
                                f"{station_choices[station_id][1]:.1f} km"
                            ),
                            key=f"incident-station-choice-{active_incident_id}",
                        )
                        station, distance_km = station_choices[selected_station_id]
                        st.button(
                            "Send alert message",
                            key=f"send-incident-alert-{active_incident_id}",
                            use_container_width=True,
                            on_click=record_incident_station_alert,
                            args=(
                                active_incident_id,
                                incident_type,
                                address,
                                latitude,
                                longitude,
                                station["name"],
                                distance_km,
                            ),
                        )
            elif active_incident_id is None:
                st.caption("No incidents to display.")
            else:
                st.caption("Waiting for the next incident.")

            previous_incidents = st.session_state.get(
                "previous_action_incidents", []
            )
            if previous_incidents:
                st.markdown(
                    f"**Previous cases (queued) · {len(previous_incidents)}**"
                )
                for previous_incident in reversed(previous_incidents):
                    previous_id = str(previous_incident["event_id"])
                    previous_latitude = float(previous_incident["latitude"])
                    previous_longitude = float(previous_incident["longitude"])
                    with st.container(border=True):
                        st.markdown(
                            incident_hud_html(previous_incident),
                            unsafe_allow_html=True,
                        )
                        st.button(
                            "Show location on map",
                            key=f"show-previous-location-{previous_id}",
                            use_container_width=True,
                            on_click=focus_incident_on_map,
                            args=(previous_id,),
                        )
                        previous_stations = nearest_training_stations(
                            previous_latitude,
                            previous_longitude,
                            police_stations,
                            max_distance_km=35,
                        )
                        if not previous_stations:
                            st.caption("Nearby police stations unavailable")
                            st.button(
                                "Send alert message",
                                key=f"send-previous-alert-{previous_id}",
                                use_container_width=True,
                                disabled=True,
                            )
                        else:
                            st.caption("Nearby police stations:")
                            for nearby_station, station_distance in previous_stations:
                                st.markdown(
                                    f"- {html.escape(str(nearby_station['name']))} · "
                                    f"{station_distance:.1f} km"
                                )
                            previous_station_choices = {
                                str(station["station_id"]): (
                                    station,
                                    station_distance,
                                )
                                for station, station_distance in previous_stations
                            }
                            selected_previous_station_id = st.selectbox(
                                "Send alert to",
                                list(previous_station_choices),
                                format_func=lambda station_id: (
                                    f"{previous_station_choices[station_id][0]['name']} · "
                                    f"{previous_station_choices[station_id][1]:.1f} km"
                                ),
                                key=f"previous-station-choice-{previous_id}",
                            )
                            previous_station, previous_distance = (
                                previous_station_choices[
                                    selected_previous_station_id
                                ]
                            )
                            st.button(
                                "Send alert message",
                                key=f"send-previous-alert-{previous_id}",
                                use_container_width=True,
                                on_click=record_incident_station_alert,
                                args=(
                                    previous_id,
                                    str(previous_incident["incident_type"]),
                                    str(previous_incident["address"]),
                                    previous_latitude,
                                    previous_longitude,
                                    previous_station["name"],
                                    previous_distance,
                                ),
                            )

            route_incident_records = {}
            if not active_matches.empty:
                route_incident_records[active_incident_id] = (
                    active_matches.iloc[0].to_dict()
                )
            for previous_incident in previous_incidents:
                route_incident_records[str(previous_incident["event_id"])] = (
                    previous_incident
                )
            if not route_incident_records and not map_df.empty:
                route_incident_records.update(
                    {
                        str(incident["event_id"]): incident
                        for incident in map_df.head(50).to_dict(orient="records")
                    }
                )
            if route_incident_records:
                st.markdown('<div class="radar-panel"><h3>Station Route Planner</h3></div>', unsafe_allow_html=True)
                route_incident_ids = list(route_incident_records)
                route_incident_key = "route-planner-incident"
                if st.session_state.get(route_incident_key) not in route_incident_records:
                    st.session_state[route_incident_key] = route_incident_ids[0]
                selected_route_incident_id = st.selectbox(
                    "Incident",
                    route_incident_ids,
                    format_func=lambda event_id: (
                        f"{route_incident_records[event_id]['incident_type']} · "
                        f"{route_incident_records[event_id]['address']}"
                    ),
                    key=route_incident_key,
                )
                selected_route_incident = route_incident_records[
                    selected_route_incident_id
                ]
                route_stations = nearest_training_stations(
                    float(selected_route_incident["latitude"]),
                    float(selected_route_incident["longitude"]),
                    police_stations,
                    max_distance_km=35,
                )
                if not route_stations:
                    st.caption("No nearby stations are available for this incident.")
                else:
                    route_station_choices = {
                        str(station["station_id"]): (station, distance_km)
                        for station, distance_km in route_stations
                    }
                    route_station_key = (
                        f"route-station-{selected_route_incident_id}"
                    )
                    if st.session_state.get(route_station_key) not in route_station_choices:
                        st.session_state[route_station_key] = next(
                            iter(route_station_choices)
                        )
                    selected_route_station_id = st.selectbox(
                            "From station",
                        list(route_station_choices),
                        format_func=lambda station_id: (
                            f"{route_station_choices[station_id][0]['name']} · "
                                f"{route_station_choices[station_id][1]:.1f} km from incident"
                        ),
                        key=route_station_key,
                    )
                    comparison_station_choices = {
                        station_id: choice
                        for station_id, choice in route_station_choices.items()
                        if station_id != selected_route_station_id
                    }
                    selected_comparison_station_id = None
                    if comparison_station_choices:
                        comparison_key = (
                            f"route-compare-{selected_route_incident_id}-"
                            f"{selected_route_station_id}"
                        )
                        if st.session_state.get(comparison_key) not in comparison_station_choices:
                            st.session_state[comparison_key] = next(
                                iter(comparison_station_choices)
                            )
                        selected_comparison_station_id = st.selectbox(
                                "Compare station",
                            list(comparison_station_choices),
                            format_func=lambda station_id: (
                                f"{comparison_station_choices[station_id][0]['name']} · "
                                    f"{comparison_station_choices[station_id][1]:.1f} km from incident"
                            ),
                            key=comparison_key,
                        )
                        route_coordinates_shared = st.checkbox(
                            "Share exact coordinates with the configured OSRM router",
                            key="route-sharing-consent",
                        )
                    st.caption(
                            f"Router: {ROUTING_SERVICE_URL}. Road distance and ETA are "
                            "estimates without live traffic. "
                        "Enabling this sends exact incident and station coordinates "
                        "to the routing service. It does not dispatch anyone."
                    )
                    if route_coordinates_shared:
                        primary_route = road_routes.get(selected_route_station_id)
                        if primary_route is not None:
                            st.markdown(
                                    f"**Route from {html.escape(route_station_choices[selected_route_station_id][0]['name'])} to incident**"
                            )
                            route_metrics = st.columns(2)
                            route_metrics[0].metric(
                                "Road distance", f"{primary_route['distance_km']:.1f} km"
                            )
                            route_metrics[1].metric(
                                "Estimated ETA",
                                f"{max(1, round(primary_route['duration_minutes']))} min",
                            )
                        else:
                            st.warning(
                                route_errors.get(
                                    selected_route_station_id,
                                    "Road route unavailable. Check the routing service.",
                                )
                            )
                        if selected_comparison_station_id is not None:
                            comparison_route = road_routes.get(
                                selected_comparison_station_id
                            )
                            if comparison_route is not None:
                                st.markdown(
                                        f"**Compare from {html.escape(comparison_station_choices[selected_comparison_station_id][0]['name'])} to incident**"
                                )
                                comparison_metrics = st.columns(2)
                                comparison_metrics[0].metric(
                                    "Road distance",
                                    f"{comparison_route['distance_km']:.1f} km",
                                )
                                comparison_metrics[1].metric(
                                    "Estimated ETA",
                                    f"{max(1, round(comparison_route['duration_minutes']))} min",
                                )
                            else:
                                st.caption(
                                    route_errors.get(
                                        selected_comparison_station_id,
                                        "Comparison road route unavailable.",
                                    )
                                )
                    else:
                        st.info("Enable coordinate sharing to load road routes and ETA.")
                    st.button(
                        "Focus selected incident on map",
                        key=f"focus-route-{selected_route_incident_id}",
                        use_container_width=True,
                        on_click=focus_incident_on_map,
                        args=(selected_route_incident_id,),
                    )

render_live_dashboard()
