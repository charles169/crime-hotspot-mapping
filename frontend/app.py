import asyncio
import html
import hmac
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
from dotenv import load_dotenv
from folium.plugins import HeatMap, MarkerCluster

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

with (Path(__file__).resolve().parents[1] / "backend" / "data" / "tamil_nadu.geojson").open(
    encoding="utf-8"
) as boundary_file:
    TAMIL_NADU_FEATURE = json.load(boundary_file)


class TacticalTileStyle(MacroElement):
    _template = Template(
        """{% macro html(this, kwargs) %}
        <style>.leaflet-container {background:#07111d;}
        .leaflet-fade-anim .leaflet-tile,
        .leaflet-fade-anim .leaflet-popup {transition:none !important;}
        .incident-cluster-icon {align-items:center;background:#123044;
            border:2px solid #35c9dc;border-radius:50%;box-shadow:0 0 10px #35c9dc66;
            display:flex;height:34px;justify-content:center;width:34px;}
        .incident-cluster-dot {background:#89a9bd;border:2px solid #fff;
            border-radius:50%;box-shadow:0 0 8px #89a9bd99;height:12px;width:12px;}
        .incident-map-marker {background:#8795a1;border:2px solid #fff;
            border-radius:50%;box-shadow:0 0 8px #8795a199;height:12px;width:12px;}
        .incident-map-marker.priority-high {background:#ff354d;color:#ff354d;
            box-shadow:0 0 8px #ff354d99;}
        .incident-map-marker.priority-medium {background:#ff9f43;color:#ff9f43;
            box-shadow:0 0 8px #ff9f4399;}
        .incident-map-marker.priority-low {background:#48d597;color:#48d597;
            box-shadow:0 0 8px #48d59799;}
        .incident-map-marker.selected {border:3px solid #fff;
            box-shadow:0 0 12px currentColor;}
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


class IncidentHeatmapRegistry(MacroElement):
    _template = Template(
        """{% macro script(this, kwargs) %}
        window.crimeRadarIncidentHeatmap = {{this.heatmap.get_name()}};
        {% endmacro %}"""
    )

    def __init__(self, heatmap):
        super().__init__()
        self._name = "IncidentHeatmapRegistry"
        self.heatmap = heatmap


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
          const incidentHeatmap = window.crimeRadarIncidentHeatmap;
          const trainingMarkers = {};
          let lastPayload = "";
          let lastFocus = "";
          const incidentIcon = function (markerClass, selected) {
            const size = selected ? 22 : 18;
            const anchor = size / 2;
            return L.divIcon({
              className: "",
              html: '<div class="incident-map-marker ' + markerClass +
                (selected ? ' selected' : '') + '"></div>',
              iconSize: [size, size],
              iconAnchor: [anchor, anchor]
            });
          };
          const syncMapState = function () {
            const payload =
              window.parent.sessionStorage.getItem("crime-radar-training-cases") || "[]";
            if (payload !== lastPayload) {
              const incoming = JSON.parse(payload);
              const activeIds = new Set();
              incoming.forEach(function (incident) {
                const id = String(incident.event_id);
                activeIds.add(id);
                const markerClass = incident.marker_class || "priority-unknown";
                if (trainingMarkers[id]) {
                  const existingMarker = window.crimeRadarMarkers[id] &&
                    window.crimeRadarMarkers[id].marker;
                  if (existingMarker && trainingMarkers[id].popupHtml !== incident.popup_html) {
                    existingMarker.setPopupContent(incident.popup_html);
                    trainingMarkers[id].popupHtml = incident.popup_html;
                  }
                  if (existingMarker && trainingMarkers[id].markerClass !== markerClass) {
                    const selected = window.crimeRadarSelectedMarkerId === id;
                    const icon = incidentIcon(markerClass, selected);
                    existingMarker.setIcon(icon);
                    trainingMarkers[id].markerClass = markerClass;
                    window.crimeRadarMarkers[id].normalIcon =
                      incidentIcon(markerClass, false);
                    window.crimeRadarMarkers[id].markerClass = markerClass;
                  }
                  return;
                }
                const position = [incident.latitude, incident.longitude];
                const normalIcon = incidentIcon(markerClass, false);
                const marker = L.marker(position, {
                  icon: normalIcon,
                  title: incident.incident_type
                }).bindPopup(incident.popup_html);
                incidentCluster.addLayer(marker);
                trainingMarkers[id] = {
                  marker: marker,
                  popupHtml: incident.popup_html,
                  markerClass: markerClass
                };
                window.crimeRadarMarkers[id] = {
                  marker: marker,
                  normalIcon: normalIcon,
                  markerClass: markerClass
                };
              });
              Object.keys(trainingMarkers).forEach(function (id) {
                if (activeIds.has(id)) return;
                incidentCluster.removeLayer(trainingMarkers[id].marker);
                delete trainingMarkers[id];
                delete window.crimeRadarMarkers[id];
              });
              if (incidentHeatmap) {
                incidentHeatmap.setLatLngs(incoming
                .filter(function (incident) { return !incident.is_demo; })
                .map(function (incident) {
                  return [incident.latitude, incident.longitude];
                }));
              }
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
              marker.setIcon(incidentIcon(entry.markerClass || "priority-unknown", true));
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


def incident_marker_class(priority):
    normalized_priority = str(priority or "").strip().upper()
    if normalized_priority in {"HIGH", "CRITICAL"}:
        return "priority-high"
    if normalized_priority == "MEDIUM":
        return "priority-medium"
    if normalized_priority == "LOW":
        return "priority-low"
    return "priority-unknown"


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
        position: fixed !important;
        left: -336px;
        top: 72px;
        bottom: 16px;
        height: calc(100vh - 88px);
        width: min(360px, 88vw) !important;
        z-index: 10000;
        overflow-y: auto;
        overscroll-behavior-y: contain;
        scrollbar-width: thin;
        scrollbar-color: #31586a #091522;
        -webkit-overflow-scrolling: touch;
        padding: 12px;
        background: #091522;
        border: 1px solid #1b3a4d;
        border-left: 0;
        border-radius: 0 10px 10px 0;
        box-shadow: 8px 0 24px rgba(0, 0, 0, .38);
        transition: left .24s cubic-bezier(.2, .8, .2, 1);
    }
    div[data-testid="stHorizontalBlock"]:has(iframe[title="st.iframe"][height="700"])
        > div[data-testid="column"]:first-child:hover {
        left: 0;
    }
    div[data-testid="stHorizontalBlock"]:has(iframe[title="st.iframe"][height="700"])
        > div[data-testid="column"]:first-child::after {
        content: "FILTERS";
        position: absolute;
        right: -25px;
        top: 50%;
        padding: 10px 5px;
        color: #b7eaf1;
        background: #123044;
        border: 1px solid #1b3a4d;
        border-left: 0;
        border-radius: 0 6px 6px 0;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: .08em;
        writing-mode: vertical-rl;
        pointer-events: auto;
    }
    @media (max-width: 700px) {
        div[data-testid="stHorizontalBlock"]:has(iframe[title="st.iframe"][height="700"])
            > div[data-testid="column"]:first-child {
            width: min(340px, 92vw) !important;
            height: calc(100vh - 88px);
            left: calc(-min(340px, 92vw) + 24px);
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

LOGIN_USERNAME = os.getenv("CRIME_RADAR_LOGIN_USERNAME", "").strip()
LOGIN_PASSWORD = os.getenv("CRIME_RADAR_LOGIN_PASSWORD", "")


def render_login_page():
    st.markdown(
        """
        <style>
        [data-testid="stMainBlockContainer"] {
            max-width: 1120px;
            padding-top: 5vh;
        }
        .login-brand {
            min-height: 410px;
            padding: 34px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            position: relative;
            overflow: hidden;
            background-color: #0a1927;
            background-image:
                repeating-linear-gradient(0deg, transparent 0 31px, #17304455 32px),
                repeating-linear-gradient(90deg, transparent 0 31px, #17304455 32px);
            border: 1px solid #1b3a4d;
            border-radius: 8px;
        }
        .login-brand-top, .login-brand-bottom { position: relative; z-index: 1; }
        .login-eyebrow {
            color: #35c9dc;
            font-size: .72rem;
            font-weight: 700;
            letter-spacing: .14em;
        }
        .login-brand h1 {
            color: #e3edf5;
            font-size: 2.7rem;
            line-height: 1.05;
            margin: 22px 0 12px;
        }
        .login-brand-copy { color: #9bb4c4; font-size: 1rem; max-width: 380px; }
        .login-scope {
            display: flex;
            gap: 9px;
            align-items: center;
            color: #ffc56c;
            font-size: .76rem;
            font-weight: 700;
        }
        .login-scope-mark {
            display: inline-block;
            width: 8px;
            height: 8px;
            background: #ffc56c;
            border-radius: 50%;
            box-shadow: 0 0 0 4px #ffc56c22;
        }
        .login-form-title { color: #e3edf5; font-size: 1.45rem; font-weight: 700; }
        .login-form-copy { color: #86a4b8; margin-bottom: 1.25rem; }
        div[data-testid="stAlert"] {
            background: #292417 !important;
            border: 1px solid #765b2a !important;
        }
        div[data-testid="stAlert"] p { color: #ffcf78 !important; }
        div[data-testid="stCaptionContainer"] p { color: #9bb4c4 !important; }
        div[data-testid="stTextInput"] label { color: #c4d7e3 !important; }
        div[data-testid="stTextInput"] input {
            background: #0d2232 !important;
            color: #e3edf5 !important;
            border-color: #31586a !important;
        }
        div[data-testid="stFormSubmitButton"] button {
            background: #35c9dc;
            color: #07111d;
            border: 0;
            font-weight: 700;
        }
        div[data-testid="stFormSubmitButton"] button:disabled {
            background: #203642;
            color: #91a8b5;
            opacity: 1;
        }
        @media (max-width: 700px) {
            [data-testid="stMainBlockContainer"] { padding: 1rem .75rem; }
            .login-brand { min-height: 270px; padding: 24px; }
            .login-brand h1 { font-size: 2.2rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    brand_column, form_column = st.columns([1.1, 0.9], gap="large")
    with brand_column:
        st.markdown(
            """
            <section class="login-brand">
              <div class="login-brand-top">
                <div class="login-eyebrow">SECURE OPERATIONS ACCESS</div>
                <h1>C.R.I.M.E.<br>Radar</h1>
                <p class="login-brand-copy">
                  Coordinated incident awareness for Tamil Nadu.
                </p>
              </div>
              <div class="login-brand-bottom">
                <div class="login-scope">
                  <span class="login-scope-mark"></span>
                  PRIVATE OPERATOR DASHBOARD · TAMIL NADU ONLY
                </div>
              </div>
            </section>
            """,
            unsafe_allow_html=True,
        )
    with form_column:
        st.markdown(
            '<div class="login-form-title">Operator sign in</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="login-form-copy">Use your assigned dashboard credentials.</div>',
            unsafe_allow_html=True,
        )
        login_configured = bool(LOGIN_USERNAME and LOGIN_PASSWORD)
        if not login_configured:
            st.warning("Operator login is not configured for this installation.")
            st.caption(
                "Add CRIME_RADAR_LOGIN_USERNAME and CRIME_RADAR_LOGIN_PASSWORD "
                "to the ignored .env file, then restart the dashboard."
            )

        with st.form("operator-login"):
            username = st.text_input("Operator ID", autocomplete="username")
            password = st.text_input(
                "Password", type="password", autocomplete="current-password"
            )
            submitted = st.form_submit_button(
                "Sign in",
                use_container_width=True,
                disabled=not login_configured,
            )

        if submitted:
            username_matches = hmac.compare_digest(username, LOGIN_USERNAME)
            password_matches = hmac.compare_digest(password, LOGIN_PASSWORD)
            if username_matches and password_matches:
                st.session_state["authenticated"] = True
                st.rerun()
            st.error("Credentials not recognized.")


if not st.session_state.get("authenticated", False):
    render_login_page()
    st.stop()

header_left, header_right = st.columns([4, 1])
with header_left:
    st.markdown('<p class="radar-title">CRIME HOTSPOT MAPPING — TAMIL NADU</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="radar-source-note">C.R.I.M.E. RADAR · COORDINATED RESPONSE & INCIDENT MAPPING ENGINE</p>',
        unsafe_allow_html=True,
    )
with header_right:
    if st.button("Sign out", use_container_width=True):
        st.session_state.clear()
        st.rerun()
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
        left_panel, map_panel, right_panel = st.columns([1.3, 3.4, 1.3], gap="small")
        with left_panel:
            st.markdown('<div class="radar-panel"><h3>Predictive Analytics</h3></div>', unsafe_allow_html=True)
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
            alert_level = "HIGH RISK" if demo_critical_count else (
                "HIGH RISK" if real_critical_count else "MONITORING"
            )
            st.metric("Current alert level", alert_level)
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
                f"**Latest report:** "
                f"{html.escape(latest_incident_type)}"
            )
            st.caption(
                f"Agency: {real_count} · User reports: {report_count} · "
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
            selected_type = st.selectbox("Incident type", ["All types", *incident_types])
            selected_priority = st.selectbox("Priority", ["All priorities", *priorities])
            selected_status = st.selectbox("Status", ["All statuses", *incident_statuses])
            show_patrol_units = st.checkbox("Show patrol telemetry", value=True)

        filtered_df = df.copy()
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
            is_report = bool(incident["is_report"])
            incident_type = html.escape(
                display_incident_type(incident["incident_type"], is_demo)
            )
            incident_address = html.escape(
                display_incident_address(incident["address"], is_demo)
            )
            report_details = str(incident.get("report_details") or "").strip()
            report_context = (
                "<br><b>SIMULATED TRAINING INCIDENT · NOT A VERIFIED CRIME</b>"
                if is_demo
                else "<br><b>UNVERIFIED USER REPORT</b>"
                if is_report
                else ""
            )
            if is_report and report_details:
                report_context += f"<br><b>Details:</b> {html.escape(report_details)}"
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
                    "marker_class": incident_marker_class(incident.get("priority")),
                    "is_demo": is_demo,
                    "popup_html": (
                        f"{'<b>ACTIVE INCIDENT</b><br>' if event_id == active_action_incident_id else ''}"
                        f"<b>Crime type:</b> {incident_type}<br>"
                        f"<b>Location:</b> {incident_address}<br>"
                        f"<b>Date:</b> "
                        f"{html.escape(display_incident_time(incident['timestamp']))}<br>"
                        f"<b>Incident ID:</b> "
                        f"{html.escape(display_event_id(event_id, is_demo))}<br>"
                        f"<b>Priority:</b> {html.escape(str(incident['priority']))}<br>"
                        f"<b>Report status:</b> "
                        f"{html.escape(display_incident_status(incident['status']))}"
                        f"{report_context}"
                        f"{dispatch_detail}"
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
            st.markdown(
                '<div class="radar-panel"><h3>Map Legend</h3><small>'
                '<span style="color:#ff354d">●</span> High / critical priority<br>'
                '<span style="color:#ff9f43">●</span> Medium priority<br>'
                '<span style="color:#48d597">●</span> Low priority<br>'
                '<span style="color:#8795a1">●</span> Unclassified priority<br>'
                '<span style="color:#35c9dc">●</span> Incident cluster<br>'
                'Heatmap · concentration of non-simulated reports; user reports are unverified<br>'
                'Blue PD badge · police station<br>'
                'Cyan line · received patrol telemetry only<br>'
                'Blue marker · received patrol asset</small></div>',
                unsafe_allow_html=True,
            )
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
                    "Map focus", list(city_centers), index=0,
                    label_visibility="collapsed", key="map-city-focus"
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
            incident_heatmap = HeatMap(
                data=[],
                name="Incident concentration",
                radius=28,
                blur=22,
                min_opacity=0.25,
                gradient={
                    "0.2": "#35c9dc",
                    "0.5": "#ffc247",
                    "0.8": "#ff9f43",
                    "1.0": "#ff354d",
                },
            ).add_to(radar_map)
            IncidentHeatmapRegistry(incident_heatmap).add_to(radar_map.get_root())
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
            folium.GeoJson(
                TAMIL_NADU_FEATURE,
                name="Tamil Nadu boundary",
                style_function=lambda _: {
                    "color": "#35e0ff",
                    "weight": 2.5,
                    "fillColor": "#063d4c",
                    "fillOpacity": 0.16,
                },
            ).add_to(radar_map)
            station_reference_id = (
                st.session_state.get("map_focus_event_id")
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
            incident_area_station_ids = set()
            if station_reference is not None:
                incident_area_station_ids = {
                    station["station_id"]
                    for station, _ in nearest_training_stations(
                        float(station_reference["latitude"]),
                        float(station_reference["longitude"]),
                        police_stations,
                        max_distance_km=35,
                    )
                }
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
                    incident_location = [incident["latitude"], incident["longitude"]]
                    incident_name = html.escape(
                        display_incident_type(incident["incident_type"], is_demo)
                    )
                    incident_address = html.escape(
                        display_incident_address(incident["address"], is_demo)
                    )
                    incident_id = str(incident["event_id"])
                    report_details = str(incident.get("report_details") or "").strip()
                    report_context = (
                        "<br><b>SIMULATED TRAINING INCIDENT · NOT A VERIFIED CRIME</b>"
                        if is_demo
                        else "<br><b>UNVERIFIED USER REPORT</b>"
                        if is_report
                        else ""
                    )
                    if is_report and report_details:
                        report_context += (
                            f"<br><b>Details:</b> {html.escape(report_details)}"
                        )
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
                    marker_classes = [
                        "incident-map-marker",
                        incident_marker_class(incident.get("priority")),
                    ]
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
                            f"<b>Crime type:</b> {incident_name}<br>"
                            f"<b>Location:</b> {incident_address}<br>"
                            f"<b>Date:</b> "
                            f"{html.escape(display_incident_time(incident['timestamp']))}<br>"
                            f"<b>Incident ID:</b> "
                            f"{html.escape(display_event_id(incident_id, is_demo))}<br>"
                            f"<b>Priority:</b> {html.escape(str(incident['priority']))}<br>"
                            f"<b>Report status:</b> "
                            f"{html.escape(display_incident_status(incident['status']))}"
                            f"{report_context}"
                            f"{dispatch_detail}",
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
                        assigned_location = [
                            assigned_incident["latitude"],
                            assigned_incident["longitude"],
                        ]
                        distance = haversine(
                            assigned_incident["longitude"],
                            assigned_incident["latitude"],
                            unit["longitude"],
                            unit["latitude"],
                        )
                        folium.PolyLine(
                            locations=[assigned_location, unit_location],
                            color="#22cce4",
                            weight=2,
                            opacity=0.85,
                            dash_array="6, 8",
                            tooltip=f"Lines of bearing · {distance:.2f} km",
                        ).add_to(radar_map)
                        unit_popup += f"<br>Intercept distance: {distance:.2f} km"
                        unit_tooltip += f" · {distance:.1f} km"
                    elif is_demo_unit and assigned_to:
                        assigned_incident = next(
                            incident
                            for incident in demo_critical_incidents
                            if str(incident["event_id"]) == assigned_to
                        )
                        assigned_location = [
                            assigned_incident["latitude"],
                            assigned_incident["longitude"],
                        ]
                        distance = haversine(
                            assigned_incident["longitude"],
                            assigned_incident["latitude"],
                            unit["longitude"],
                            unit["latitude"],
                        )
                        folium.PolyLine(
                            locations=[unit_location, assigned_location],
                            color="#ffc247",
                            weight=3,
                            opacity=0.95,
                            dash_array="8, 6",
                            tooltip=f"Assignment · {distance:.2f} km",
                        ).add_to(radar_map)
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
            folium.LayerControl(position="topright", collapsed=True).add_to(radar_map)
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
                    "map_mode": "priority-heatmap-v20",
                    "city": map_city,
                    "show_training": show_demo_data,
                    "show_patrol_units": show_patrol_units,
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
                        f"**{html.escape(incident_type)}** · {html.escape(address)}"
                    )
                    st.caption(
                        f"{display_incident_time(active_incident['timestamp'])} · "
                        f"{latitude:.5f}, {longitude:.5f}"
                    )
                    st.button(
                        "Show location on map",
                        key=f"show-incident-location-{active_incident_id}",
                        use_container_width=True,
                        on_click=focus_incident_on_map,
                        args=(active_incident_id,),
                    )
                    details = str(active_incident.get("report_details") or "").strip()
                    if details:
                        st.caption(details)
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
                    previous_type = html.escape(
                        str(previous_incident["incident_type"])
                    )
                    previous_address = html.escape(
                        str(previous_incident["address"])
                    )
                    previous_latitude = float(previous_incident["latitude"])
                    previous_longitude = float(previous_incident["longitude"])
                    with st.container(border=True):
                        st.markdown(
                            f"**{previous_type}** · {previous_address}"
                        )
                        st.caption(
                            f"{display_incident_time(previous_incident['timestamp'])} · "
                            f"{previous_latitude:.5f}, {previous_longitude:.5f}"
                        )
                        previous_details = str(
                            previous_incident.get("report_details") or ""
                        ).strip()
                        if previous_details:
                            st.write(previous_details)
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

render_live_dashboard()
