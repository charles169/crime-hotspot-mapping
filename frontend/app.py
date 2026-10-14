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
        .leaflet-container .leaflet-tile-pane {filter: brightness(.27)
            saturate(1.9) contrast(1.35) hue-rotate(170deg);}
        .incident-cluster-icon {align-items:center;background:#11384a;
            border:2px solid #36cde2;border-radius:50%;box-shadow:0 0 10px #19bdd566;
            display:flex;height:34px;justify-content:center;width:34px;}
        .incident-cluster-dot {background:#24c8dc;border:2px solid #e8fbff;
            border-radius:50%;box-shadow:0 0 8px #24c8dc99;height:12px;width:12px;}
        .incident-map-marker {background:#24c8dc;border:2px solid #e8fbff;
            border-radius:50%;box-shadow:0 0 8px #24c8dc99;height:12px;width:12px;}
        .incident-map-marker.critical {background:#ff5364;box-shadow:0 0 9px #ff536499;}
        .incident-map-marker.selected {background:#ff354d;border:3px solid #fff;
            box-shadow:0 0 12px #ff354d;}
        </style>
        {% endmacro %}"""
    )


class IncidentMarkerRegistry(MacroElement):
    _template = Template(
        """{% macro script(this, kwargs) %}
        window.crimeRadarMarkers = window.crimeRadarMarkers || {};
        window.crimeRadarMarkers[{{this.event_id_json}}] = {
          marker: {{this.marker.get_name()}},
          normalIcon: {{this.marker.get_name()}}.getIcon(),
          critical: {{this.critical_json}}
        };
        {{this.marker.get_name()}}.on("click", function () {
          if (window.crimeRadarClearIncidentHeat) {
            window.crimeRadarClearIncidentHeat();
          }
          if (window.crimeRadarShowContainment) {
            window.crimeRadarShowContainment(
              {{this.marker.get_name()}},
              {{this.critical_json}}
            );
          }
        });
        {% endmacro %}"""
    )

    def __init__(self, event_id, marker, critical):
        super().__init__()
        self._name = "IncidentMarkerRegistry"
        self.event_id_json = json.dumps(event_id)
        self.marker = marker
        self.critical_json = json.dumps(critical)


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
        window.crimeRadarIncidentHeatLayer = {{this.layer.get_name()}};
        {% endmacro %}"""
    )

    def __init__(self, layer):
        super().__init__()
        self._name = "IncidentHeatmapRegistry"
        self.layer = layer


class MapFocusWatcher(MacroElement):
    _template = Template(
        """{% macro script(this, kwargs) %}
        (function () {
          const map = {{this._parent.get_name()}};
          const city = {{this.city_json}};
          const showTraining = {{this.show_training_json}};
          const activeAlertCaseId = {{this.active_alert_case_id_json}};
          const viewKey = "crime-radar-map-view";
          const savedView = window.parent.sessionStorage.getItem(viewKey);
          if (savedView && !activeAlertCaseId) {
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
          const clearIncidentHeat = function () {
            const incidentHeat = window.crimeRadarIncidentHeatLayer;
            if (incidentHeat && map.hasLayer(incidentHeat)) {
              map.removeLayer(incidentHeat);
            }
            if (trainingHeatLayer && map.hasLayer(trainingHeatLayer)) {
              map.removeLayer(trainingHeatLayer);
            }
          };
          window.crimeRadarShowContainment = function (marker, critical) {
            if (window.crimeRadarContainmentRing) {
              map.removeLayer(window.crimeRadarContainmentRing);
              window.crimeRadarContainmentRing = null;
            }
            if (critical) {
              window.crimeRadarContainmentRing = L.circle(marker.getLatLng(), {
                radius: 1500,
                color: "#ff354d",
                weight: 2,
                fillColor: "#ff354d",
                fillOpacity: 0.08
              }).addTo(map).bindTooltip("Critical containment zone · 1.50 km radius");
            }
          };
          const incidentCluster = window.crimeRadarIncidentMarkerCluster;
          const trainingHeatLayer = showTraining
            ? L.heatLayer([], {
                radius: 15,
                blur: 12,
                minOpacity: 0.18,
                gradient: {
                  0.25: "#168de2",
                  0.55: "#22bad1",
                  0.8: "#35e0ff",
                  1.0: "#8af2ff"
                }
              }).addTo(map)
            : null;
          window.crimeRadarTrainingHeatLayer = trainingHeatLayer;
          window.crimeRadarClearIncidentHeat = clearIncidentHeat;
          const trainingMarkers = {};
          let selectedMarkerId = null;
          const counter = showTraining ? L.control({position: "topright"}) : null;
          if (counter) {
            counter.onAdd = function () {
              this.container = L.DomUtil.create("div", "training-case-counter");
              this.container.style.cssText =
                "background:#101c29;color:#ffd06e;padding:7px 10px;" +
                "border:1px solid #6a5527;border-radius:5px;" +
                "font:700 12px sans-serif";
              this.container.textContent = "NEW REPORTS: 0";
              return this.container;
            };
            counter.addTo(map);
          }
          let lastPayload = "";
          let lastFocus = "";
          window.setInterval(function () {
            const params = new URLSearchParams(window.parent.location.search);
            const payload =
              window.parent.sessionStorage.getItem("crime-radar-training-cases") || "[]";
            if (showTraining && payload !== lastPayload) {
              const incoming = JSON.parse(payload);
              const activeIds = new Set();
              incoming.forEach(function (incident) {
                const id = String(incident.event_id);
                activeIds.add(id);
                if (trainingMarkers[id]) {
                  const existingMarker = window.crimeRadarMarkers[id] &&
                    window.crimeRadarMarkers[id].marker;
                  if (existingMarker) {
                    existingMarker.setPopupContent(incident.popup_html);
                  }
                  return;
                }
                const position = [incident.latitude, incident.longitude];
                const critical = incident.priority === "CRITICAL";
                const normalIcon = L.divIcon({
                  className: "",
                  html: '<div class="incident-map-marker' +
                    (critical ? ' critical' : '') + '"></div>',
                  iconSize: [18, 18],
                  iconAnchor: [9, 9]
                });
                const marker = L.marker(position, {
                  icon: normalIcon,
                  title: incident.incident_type
                }).bindPopup(incident.popup_html);
                marker.on("click", function () {
                  clearIncidentHeat();
                  window.crimeRadarShowContainment(marker, critical);
                });
                incidentCluster.addLayer(marker);
                trainingMarkers[id] = {
                  marker: marker
                };
                window.crimeRadarMarkers[id] = {
                  marker: marker,
                  normalIcon: normalIcon,
                  critical: critical
                };
              });
              Object.keys(trainingMarkers).forEach(function (id) {
                if (activeIds.has(id)) return;
                incidentCluster.removeLayer(trainingMarkers[id].marker);
                delete trainingMarkers[id];
                delete window.crimeRadarMarkers[id];
              });
              counter.getContainer().textContent =
                "NEW REPORTS: " + incoming.length;
              trainingHeatLayer.setLatLngs(incoming.map(function (incident) {
                return [incident.latitude, incident.longitude];
              }));
              lastPayload = payload;
            }
            const focus = activeAlertCaseId || params.get("focus") || "";
            const focusToken = focus + "|" + (params.get("focus_nonce") || "");
            if (!focus || focusToken === lastFocus) return;
            const entry = window.crimeRadarMarkers &&
              window.crimeRadarMarkers[focus];
            if (entry) {
              clearIncidentHeat();
              const previousId = window.crimeRadarSelectedMarkerId;
              const previouslySelected = window.crimeRadarMarkers[previousId];
              if (previouslySelected && previousId !== focus) {
                previouslySelected.marker.setIcon(previouslySelected.normalIcon);
              }
              const marker = entry.marker;
              window.crimeRadarShowContainment(
                marker,
                entry.critical
              );
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
                duration: 1.1
              });
              window.setTimeout(() => marker.openPopup(), 1150);
            }
          }, 250);
        })();
        {% endmacro %}"""
    )

    def __init__(self, city, show_training, active_alert_case_id=None):
        super().__init__()
        self._name = "MapFocusWatcher"
        self.city_json = json.dumps(city)
        self.show_training_json = json.dumps(show_training)
        self.active_alert_case_id_json = json.dumps(active_alert_case_id)


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


def nearest_training_stations(latitude, longitude, stations, max_count=5, exclude_station_id=None):
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
        results.append((station, distance_km))
        if len(results) >= max_count:
            break
    return results


def focus_incident_on_map(event_id):
    st.session_state["map_focus_event_id"] = str(event_id)
    st.query_params["focus"] = str(event_id)
    st.query_params["focus_nonce"] = str(time.time_ns())


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


def training_map_sync_html(training_cases):
    payload = json.dumps(training_cases, separators=(",", ":"), ensure_ascii=True)
    return (
        "<script>window.frameElement.style.display='none';"
        "window.parent.sessionStorage.setItem("
        "'crime-radar-training-cases', "
        f"{json.dumps(payload)}"
        ");</script>"
    )


def display_patrol_status(status):
    return "AVAILABLE" if is_training_status(status) else str(status)


API_URL = os.getenv("CRIME_RADAR_API_URL", "http://127.0.0.1:8000").rstrip("/")
REFRESH_SECONDS = 2


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
    iframe[title="st.iframe"][height="700"] {
        border: 1px solid #1b3a4d;
        border-radius: 8px;
        box-shadow: 0 0 22px rgba(8, 184, 211, .08);
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

refresh_started_at = time.monotonic()
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
        agency_df = incoming_df[~incoming_df["is_demo"]].copy()
        persisted_demo_df = incoming_df[incoming_df["is_demo"]].copy()
    else:
        agency_df = incoming_df
        persisted_demo_df = incoming_df
    demo_df = (
        persisted_demo_df
        if show_demo_data
        else pd.DataFrame()
    )
    df = pd.concat([agency_df, demo_df], ignore_index=True)
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

    known_critical_ids = st.session_state.get("known_critical_incident_ids")
    if known_critical_ids is None:
        newly_detected_critical = []
        known_critical_ids = current_critical_ids
    else:
        newly_detected_critical = [
            incident
            for incident in current_critical
            if str(incident["event_id"]) not in known_critical_ids
        ]
        known_critical_ids.update(current_critical_ids)
    st.session_state["known_critical_incident_ids"] = known_critical_ids
    for incident in newly_detected_critical:
        is_training = bool(incident["is_demo"])
        incident_type = display_incident_type(incident["incident_type"], is_training)
        address = display_incident_address(incident["address"], is_training)
        st.toast(
            f"{incident_type} near {address}",
            icon="🚨",
        )

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
    newly_assigned = set()
    for _, event_id, unit_id in candidate_assignments:
        if event_id not in assignments and unit_id not in used_unit_ids:
            assignments[event_id] = unit_id
            used_unit_ids.add(unit_id)
            newly_assigned.add(event_id)

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

    if newly_assigned:
        notified_incidents = st.session_state.setdefault("demo_dispatch_notified", set())
        pending_notifications = newly_assigned - notified_incidents
        if pending_notifications:
            event_id = sorted(pending_notifications)[0]
            incident = next(
                item for item in demo_critical_incidents
                if str(item["event_id"]) == event_id
            )
            dispatch = dispatch_by_incident[event_id]
            incident_type = display_incident_type(incident["incident_type"], True)
            incident_address = display_incident_address(incident["address"], True)
            station_message = (
                f"from {dispatch['station']['name']} "
                f"({dispatch['station_distance_km']:.2f} km). "
                if dispatch["station"] is not None
                else "No station location is available. "
            )
            st.toast(
                f"{incident_type} near {incident_address}. "
                f"{str(dispatch['unit']['unit_id']).replace('SIM-TN-UNIT-', 'UNIT-')} assigned "
                f"{station_message}"
                "No police contacted.",
                icon="🚨",
            )
        notified_incidents.update(newly_assigned)

    incident_types = sorted(df["incident_type"].dropna().unique()) if not df.empty else []
    priorities = sorted(df["priority"].dropna().unique()) if not df.empty else []
    incident_statuses = (
        sorted(df["status"].dropna().astype(str).unique()) if not df.empty else []
    )

    with left_panel:
        critical_count = int((df["priority"].str.upper() == "CRITICAL").sum()) if not df.empty else 0
        real_count = int((~df["is_demo"]).sum()) if not df.empty else 0
        demo_count = int(df["is_demo"].sum()) if not df.empty else 0
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
            f"Agency: {real_count} · Incidents: {demo_count} · "
            f"Critical: {critical_count} · Auto-refresh active"
        )
        if show_demo_data:
            st.caption(
                "Incident records loaded from the database"
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
    map_focus_ids = {
        str(event_id)
        for event_id in (focused_event_id, current_alert_id)
        if event_id is not None
    }
    for event_id in map_focus_ids:
        if not df.empty and not map_df["event_id"].astype(str).eq(event_id).any():
            focus_rows = df[df["event_id"].astype(str) == event_id]
            if not focus_rows.empty:
                map_df = pd.concat([map_df, focus_rows], ignore_index=True)
    if current_alert_id is not None:
        map_df = map_df[
            map_df["event_id"].astype(str).eq(current_alert_id)
        ].copy()

    training_map_events = (
        map_df[map_df["is_demo"].astype(bool)].to_dict(orient="records")
        if not map_df.empty and "is_demo" in map_df
        else []
    )
    if show_demo_data:
        training_map_events = [
            incident
            for incident in training_map_events
            if str(incident["event_id"]) == current_alert_id
        ]
    training_map_payload = []
    for incident in training_map_events:
        event_id = str(incident["event_id"])
        incident_type = html.escape(
            display_incident_type(incident["incident_type"], True)
        )
        incident_address = html.escape(
            display_incident_address(incident["address"], True)
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
                "popup_html": (
                    f"{'<b>CURRENT ALERT CASE</b><br>' if event_id == current_alert_id else ''}"
                    f"<b>{incident_type}</b><br>"
                    f"<b>Area:</b> {incident_address}<br>"
                    f"<b>Reported:</b> "
                    f"{html.escape(display_incident_time(incident['timestamp']))}<br>"
                    f"{html.escape(str(incident['priority']))} · "
                    f"{html.escape(display_incident_status(incident['status']))}"
                    f"{dispatch_detail}"
                ),
            }
        )
    render_map_df = (
        map_df[~map_df["is_demo"].astype(bool)]
        if not map_df.empty and "is_demo" in map_df
        else map_df
    )
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
                    'Cyan heatmap · incident density<br>'
                    'Red dot · selected incident<br>'
                    'Red label · current alert case<br>'
                    'Red ring · critical report<br>'
                    'Cyan line · received patrol telemetry only<br>'
                    'Blue marker · received patrol asset</small></div>', unsafe_allow_html=True)
        station_alert_outbox = st.session_state.get("station_alert_outbox", [])
        with st.expander(
            f"Station Message Dashboard · {len(station_alert_outbox)}",
            expanded=False,
        ):
            st.caption(
                "Session log only; no external station messaging service is connected."
            )
            if station_alert_outbox:
                for entry in reversed(station_alert_outbox):
                    status = entry.get("status", "Sent (simulated)")
                    station_name = entry.get("station_name", "Unknown station")
                    st.markdown(f"**{status}** · {station_name}")
                    st.caption(
                        f"{entry.get('sent_at', 'Unknown time')} · "
                        f"Case {entry.get('incident_id', 'Unknown')} · "
                        f"{entry.get('incident_type', 'Incident')} · "
                        f"{entry.get('distance_km', 'Unknown')} km"
                    )
                    st.caption(entry.get("message", ""))
            else:
                st.caption("No station alert messages have been recorded yet.")

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
                f"TAMIL NADU · {demo_count} INCIDENTS"
                if show_demo_data
                else "TAMIL NADU · AGENCY DATA ONLY"
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

        current_alert_dispatch = (
            dispatch_by_incident.get(current_alert_id)
            if current_alert_id is not None
            else None
        )
        current_case_map_stations = []
        if current_alert_dispatch:
            if current_alert_dispatch["station"] is not None:
                current_case_map_stations.append(
                    (
                        current_alert_dispatch["station"],
                        current_alert_dispatch["station_distance_km"],
                    )
                )
            current_case_map_stations.extend(
                current_alert_dispatch.get("nearby_stations", [])[:1]
            )
        current_case_map_station_ids = {
            station["station_id"] for station, _ in current_case_map_stations
        }

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
            location=(
                [current_alert_incident["latitude"], current_alert_incident["longitude"]]
                if current_alert_incident is not None
                else city_centers[map_city]
            ),
            zoom_start=14 if current_alert_incident is not None else (
                6 if map_city == "Tamil Nadu" else 13
            ),
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
            "World_Street_Map/MapServer/tile/{z}/{y}/{x}",
            attr="Tiles © Esri — Sources: Esri, HERE, Garmin, USGS, "
            "OpenStreetMap contributors, and the GIS User Community",
            name="Tactical street map",
            max_zoom=16,
            max_native_zoom=16,
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
        if show_demo_data:
            for station in police_stations:
                city_center = city_centers[map_city]
                is_current_case_station = (
                    station["station_id"] in current_case_map_station_ids
                )
                if not is_current_case_station and haversine(
                    city_center[1],
                    city_center[0],
                    station["longitude"],
                    station["latitude"],
                ) > 35:
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
                    "Station location"
                )
                if nearest_incident is not None:
                    nearest_distance = haversine(
                        station["longitude"],
                        station["latitude"],
                        nearest_incident["longitude"],
                        nearest_incident["latitude"],
                    )
                    nearest_type = html.escape(
                        display_incident_type(
                            nearest_incident["incident_type"],
                            bool(nearest_incident["is_demo"]),
                        )
                    )
                    nearest_area = html.escape(
                        display_incident_address(
                            nearest_incident["address"],
                            bool(nearest_incident["is_demo"]),
                        )
                    )
                    station_details += (
                        f"<br><b>Nearest displayed incident:</b> {nearest_type}"
                        f"<br><b>Incident area:</b> {nearest_area}"
                        f"<br><b>Reported:</b> "
                        f"{html.escape(display_incident_time(nearest_incident['timestamp']))}"
                        f"<br><b>Distance:</b> {nearest_distance:.2f} km"
                    )
                if is_current_case_station:
                    alert_distance = next(
                        distance
                        for case_station, distance in current_case_map_stations
                        if case_station["station_id"] == station["station_id"]
                    )
                    station_details += (
                        f"<br><b>Current case option:</b> "
                        f"{html.escape(display_incident_type(current_alert_incident['incident_type'], True))}"
                        f" · {alert_distance:.2f} km"
                    )
                    folium.PolyLine(
                        locations=[
                            [current_alert_incident["latitude"], current_alert_incident["longitude"]],
                            [station["latitude"], station["longitude"]],
                        ],
                        color="#ffc247",
                        weight=3,
                        opacity=0.9,
                        dash_array="6, 6",
                        tooltip=f"Nearby station · {alert_distance:.2f} km",
                    ).add_to(radar_map)
                folium.Marker(
                    location=[station["latitude"], station["longitude"]],
                    popup=(
                        f"<b>{html.escape(station['name'])}</b><br>"
                        f"{station_details}"
                    ),
                    tooltip=html.escape(station["name"]),
                    icon=folium.Icon(
                        color="orange" if is_current_case_station else "cadetblue",
                        icon="building",
                        prefix="fa",
                    ),
                ).add_to(radar_map)
        if current_alert_incident is not None:
            active_case_type = html.escape(
                display_incident_type(current_alert_incident["incident_type"], True)
            )
            active_case_address = html.escape(
                display_incident_address(current_alert_incident["address"], True)
            )
            active_case_position = alert_case_queue.index(current_alert_id) + 1
            active_case_label = (
                f"CURRENT CASE · CASE {active_case_position} OF "
                f"{len(alert_case_queue)}"
            )
            folium.Marker(
                location=[
                    current_alert_incident["latitude"],
                    current_alert_incident["longitude"],
                ],
                icon=folium.DivIcon(
                    html=(
                        '<div style="display:flex;align-items:center;gap:5px;'
                        'white-space:nowrap;transform:translateY(-50%);">'
                        '<span style="width:16px;height:16px;flex:none;'
                        'background:#ff354d;border:3px solid #fff;border-radius:50%;'
                        'box-shadow:0 0 14px #ff354d;"></span>'
                        f'<span style="background:#ff354d;color:#fff;padding:5px 8px;'
                        'border:1px solid #fff;border-radius:4px;font:bold 11px sans-serif;'
                        f'box-shadow:0 0 12px #ff354d;">{active_case_label}</span></div>'
                    ),
                    icon_size=(205, 30),
                    icon_anchor=(8, 15),
                ),
                tooltip=f"{active_case_label} · {active_case_type}",
                popup=folium.Popup(
                    f"<b>{active_case_label}</b><br>"
                    f"<b>{active_case_type}</b><br>"
                    f"<b>Area:</b> {active_case_address}<br>"
                    f"<b>Reported:</b> "
                    f"{html.escape(display_incident_time(current_alert_incident['timestamp']))}",
                    max_width=280,
                ),
                z_index_offset=1000,
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
            },
        ).add_to(radar_map)
        IncidentClusterRegistry(incident_cluster).add_to(radar_map)
        if not render_map_df.empty:
            incident_heat_layer = HeatMap(
                render_map_df[["latitude", "longitude"]].values.tolist(),
                name="Incident density",
                radius=15,
                blur=12,
                min_opacity=0.18,
                gradient={
                    0.25: "#168de2",
                    0.55: "#22bad1",
                    0.8: "#35e0ff",
                    1.0: "#8af2ff",
                },
            ).add_to(radar_map)
            IncidentHeatmapRegistry(incident_heat_layer).add_to(radar_map)
            for _, incident in render_map_df.iterrows():
                is_critical = str(incident["priority"]).upper() == "CRITICAL"
                is_demo = bool(incident["is_demo"])
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
                        f"<b>{incident_name}</b><br>"
                        f"<b>Area:</b> {incident_address}<br>"
                        f"<b>Reported:</b> "
                        f"{html.escape(display_incident_time(incident['timestamp']))}<br>"
                        f"<b>Incident ID:</b> "
                        f"{html.escape(display_event_id(incident_id, is_demo))}<br>"
                        f"{html.escape(str(incident['priority']))} · "
                        f"{html.escape(display_incident_status(incident['status']))}"
                        f"{dispatch_detail}",
                        max_width=280,
                        show=False,
                    ),
                ).add_to(incident_cluster)
                IncidentMarkerRegistry(
                    incident_id, incident_marker, is_critical
                ).add_to(radar_map)
        elif show_demo_data:
            incident_heat_layer = HeatMap(
                [],
                name="Incident density",
                radius=15,
                blur=12,
                min_opacity=0.18,
                gradient={
                    0.25: "#168de2",
                    0.55: "#22bad1",
                    0.8: "#35e0ff",
                    1.0: "#8af2ff",
                },
            ).add_to(radar_map)
            IncidentHeatmapRegistry(incident_heat_layer).add_to(radar_map)

        incident_targets = (
            filtered_df.loc[~filtered_df["is_demo"]].to_dict(orient="records")
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
        components.html(
            training_map_sync_html(training_map_payload),
            height=0,
            scrolling=False,
        )
        MapFocusWatcher(
            map_city,
            show_demo_data,
            active_alert_case_id=current_alert_id,
        ).add_to(radar_map)

        map_signature = json.dumps(
            {
                "map_mode": "incremental-training-v11",
                "city": map_city,
                "incidents": (
                    render_map_df[
                        [
                            "event_id",
                            "incident_type",
                            "priority",
                            "status",
                            "latitude",
                            "longitude",
                            "address",
                            "is_demo",
                        ]
                    ].to_dict(orient="records")
                    if not render_map_df.empty
                    else []
                ),
                "units": (
                    [
                        unit
                        for unit in displayed_units
                        if not is_training_status(unit["status"])
                    ]
                    if show_patrol_units
                    else []
                ),
                "manual_training_assignments": manual_assignments,
                "show_training": show_demo_data,
                "active_alert_case_id": current_alert_id,
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
        if not alert_case_queue:
            st.caption("No unalerted critical cases in the database.")
        else:
            incident_id = alert_case_queue[0]
            incident = current_alert_incident
            if incident is None:
                st.caption("Waiting for the queued case to be available in the database.")
            else:
                case_position = next(
                    index
                    for index, queued_id in enumerate(alert_case_queue, start=1)
                    if queued_id == incident_id
                )
                st.caption(
                    f"Case {case_position} of {len(alert_case_queue)} · "
                    f"{'Training' if bool(incident['is_demo']) else 'Database'} case"
                )
                dispatch = dispatch_by_incident.get(incident_id)
                is_training_case = bool(incident["is_demo"])
                incident_type = display_incident_type(
                    incident["incident_type"], is_training_case
                )
                incident_address = display_incident_address(
                    incident["address"], is_training_case
                )
                st.button(
                    f"🚨 {incident_type} · {incident_address}",
                    key=f"locate-incident-{incident_id}",
                    use_container_width=True,
                    on_click=focus_incident_on_map,
                    args=(incident_id,),
                    type="primary",
                )
                if dispatch and dispatch["station"] is not None:
                    st.caption(
                        f"Nearest: {dispatch['station']['name']} · "
                        f"{dispatch['station_distance_km']:.2f} km"
                    )
                if dispatch and (
                    dispatch["station"] is not None
                    or dispatch.get("nearby_stations")
                ):
                    nearby_station_candidates = dispatch.get(
                        "nearby_stations", []
                    )[:1]
                    if nearby_station_candidates:
                        with st.expander(
                            f"📍 {len(nearby_station_candidates)} additional nearby station(s)"
                        ):
                            for nearby_station, dist_km in nearby_station_candidates:
                                st.caption(
                                    f"{nearby_station['name']} · {dist_km:.2f} km"
                                )
                    station_options = (
                        [(dispatch["station"], dispatch["station_distance_km"])]
                        if dispatch["station"] is not None
                        else []
                    ) + nearby_station_candidates
                    selected_station_names = st.multiselect(
                        "Select up to 2 stations to alert",
                        options=[station["name"] for station, _ in station_options],
                        default=[],
                        key=f"manual-station-select-{incident_id}",
                    )
                    selected_station_names = selected_station_names[:2]
                    selected_stations = [
                        (station, distance)
                        for station, distance in station_options
                        if station["name"] in selected_station_names
                    ]
                    if st.button(
                        f"📢 Alert {len(selected_stations)} station(s)",
                        key=f"alert-stations-{incident_id}",
                        use_container_width=True,
                        disabled=not selected_stations,
                    ):
                        alert_msg = (
                            f"INCIDENT ALERT: {incident_type} at {incident_address}. "
                            f"Reported: {display_incident_time(incident['timestamp'])}. "
                            f"Priority: {incident['priority']}. "
                            "Please respond."
                        )
                        outbox = st.session_state.setdefault("station_alert_outbox", [])
                        for nearby_station, dist_km in selected_stations:
                            outbox.append({
                                "sent_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "station_name": nearby_station["name"],
                                "incident_id": incident_id,
                                "incident_type": incident_type,
                                "distance_km": f"{dist_km:.2f}",
                                "message": alert_msg,
                                "status": "Sent (simulated)",
                            })
                        del outbox[:-50]
                        st.session_state["last_alert"] = {
                            "incident_id": incident_id,
                            "incident_type": incident_type,
                            "incident_address": incident_address,
                            "stations_count": len(selected_stations),
                            "stations": [station["name"] for station, _ in selected_stations],
                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                        }
                        alert_case_queue.remove(incident_id)
                        st.session_state["critical_alert_case_queue"] = alert_case_queue
                        st.toast(
                            f"✅ Alert sent to {len(selected_stations)} station(s) for {incident_type}",
                            icon="📢",
                        )
                        st.rerun()
                elif dispatch:
                    st.caption("No stations are available to alert for this case.")

    st.subheader("Tamil Nadu Incident Telemetry")
    if filtered_df.empty:
        st.caption("No active incidents.")
    else:
        columns = ["event_id", "incident_type", "priority", "status", "address", "timestamp"]
        telemetry_table = filtered_df[columns].copy()
        training_rows = filtered_df["is_demo"].astype(bool)
        telemetry_table.loc[training_rows, "event_id"] = telemetry_table.loc[
            training_rows, "event_id"
        ].map(lambda event_id: display_event_id(event_id, True))
        telemetry_table.loc[training_rows, "incident_type"] = telemetry_table.loc[
            training_rows, "incident_type"
        ].map(lambda incident_type: display_incident_type(incident_type, True))
        telemetry_table.loc[training_rows, "status"] = telemetry_table.loc[
            training_rows, "status"
        ].map(display_incident_status)
        telemetry_table.loc[training_rows, "address"] = telemetry_table.loc[
            training_rows, "address"
        ].map(lambda address: display_incident_address(address, True))
        st.dataframe(
            telemetry_table,
            use_container_width=True,
            hide_index=True,
        )


time.sleep(
    max(0, REFRESH_SECONDS - (time.monotonic() - refresh_started_at))
)
st.rerun()
