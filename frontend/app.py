import asyncio
import html
import json
import os
import random
import time
from datetime import datetime
from math import asin, ceil, cos, floor, radians, sin, sqrt
from pathlib import Path

import folium
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from branca.element import MacroElement, Template
from folium.plugins import HeatMap

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
        </style>
        {% endmacro %}"""
    )


class IncidentMarkerRegistry(MacroElement):
    _template = Template(
        """{% macro script(this, kwargs) %}
        window.crimeRadarMarkers = window.crimeRadarMarkers || {};
        window.crimeRadarMarkers[{{this.event_id_json}}] = {
          marker: {{this.marker.get_name()}}
        };
        {{this.marker.get_name()}}.on("click", function () {
          if (window.crimeRadarClearIncidentHeat) {
            window.crimeRadarClearIncidentHeat();
          }
        });
        {% endmacro %}"""
    )

    def __init__(self, event_id, marker):
        super().__init__()
        self._name = "IncidentMarkerRegistry"
        self.event_id_json = json.dumps(event_id)
        self.marker = marker


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
          const clearIncidentHeat = function () {
            const incidentHeat = window.crimeRadarIncidentHeatLayer;
            if (incidentHeat && map.hasLayer(incidentHeat)) {
              map.removeLayer(incidentHeat);
            }
            if (trainingHeatLayer && map.hasLayer(trainingHeatLayer)) {
              map.removeLayer(trainingHeatLayer);
            }
          };
          const trainingLayer = L.layerGroup().addTo(map);
          const trainingHeatLayer = showTraining
            ? L.heatLayer([], {
                radius: 24,
                blur: 20,
                minOpacity: 0.35,
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
              this.container.textContent = "STREAMED TRAINING: 0 · NOT LIVE";
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
                if (trainingMarkers[id]) return;
                const position = [incident.latitude, incident.longitude];
                const critical = incident.priority === "CRITICAL";
                const group = L.layerGroup().addTo(trainingLayer);
                const marker = L.circleMarker(position, {
                  radius: 7,
                  color: "#22bad1",
                  weight: 2,
                  fillColor: "#22bad1",
                  fillOpacity: 0.95
                }).bindPopup(incident.popup_html);
                marker.on("click", clearIncidentHeat);
                marker.addTo(group);
                if (critical) {
                  L.circle(position, {
                    radius: 1500,
                    color: "#ff354d",
                    weight: 2,
                    fillColor: "#ff354d",
                    fillOpacity: 0.08
                  }).addTo(group);
                }
                trainingMarkers[id] = group;
                window.crimeRadarMarkers[id] = {
                  marker: marker
                };
              });
              Object.keys(trainingMarkers).forEach(function (id) {
                if (activeIds.has(id)) return;
                trainingMarkers[id].remove();
                delete trainingMarkers[id];
                delete window.crimeRadarMarkers[id];
              });
              counter.getContainer().textContent =
                "STREAMED TRAINING: " + incoming.length + " · NOT LIVE";
              trainingHeatLayer.setLatLngs(incoming.map(function (incident) {
                return [incident.latitude, incident.longitude];
              }));
              lastPayload = payload;
            }
            const focus = params.get("focus") || "";
            const focusToken = focus + "|" + (params.get("focus_nonce") || "");
            if (!focus || focusToken === lastFocus) return;
            const entry = window.crimeRadarMarkers &&
              window.crimeRadarMarkers[focus];
            if (entry) {
              clearIncidentHeat();
              const previousId = window.crimeRadarSelectedMarkerId;
              const previouslySelected = window.crimeRadarMarkers[previousId];
              if (previouslySelected && previousId !== focus) {
                previouslySelected.marker.remove();
              }
              const marker = entry.marker;
              marker.setStyle({
                color: "#ffffff",
                weight: 3,
                fillColor: "#ff354d",
                fillOpacity: 1
              });
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

    def __init__(self, city, show_training):
        super().__init__()
        self._name = "MapFocusWatcher"
        self.city_json = json.dumps(city)
        self.show_training_json = json.dumps(show_training)


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


def focus_incident_on_map(event_id):
    st.session_state["map_focus_event_id"] = str(event_id)
    st.query_params["focus"] = str(event_id)
    st.query_params["focus_nonce"] = str(time.time_ns())


def is_training_status(status):
    return str(status).strip().upper().startswith(("SIMULATED DEMO", "TRAINING EXERCISE"))


def display_incident_status(status):
    return "TRAINING EXERCISE" if is_training_status(status) else str(status)


def display_incident_type(incident_type, is_training):
    value = str(incident_type)
    if is_training and value.strip().upper() == "SIMULATED DEMO EVENT":
        return "Training incident"
    return value


def display_incident_address(address, is_training):
    value = str(address)
    if is_training:
        for suffix in (" (simulated location)", " · SYNTHETIC DEMO"):
            value = value.replace(suffix, "")
    return value


def display_event_id(event_id, is_training):
    value = str(event_id)
    if is_training and value.upper().startswith(("SIM-", "DEMO-")):
        return f"TRAINING-{value.rsplit('-', 1)[-1]}"
    return value


def create_training_incident():
    locations = [
        ("T. Nagar", 13.0418, 80.2337),
        ("Egmore", 13.0604, 80.2496),
        ("Mylapore", 13.0337, 80.2702),
        ("Anna Nagar", 13.0850, 80.2101),
        ("Guindy", 13.0108, 80.2150),
        ("Perambur", 13.1143, 80.2326),
        ("Marina Beach", 13.0475, 80.2824),
        ("Little Mount", 13.0067, 80.2206),
    ]
    area, latitude, longitude = random.choice(locations)
    incident_type, priority = random.choice(
        [
            ("BURGLARY", "CRITICAL"),
            ("ASSAULT", "CRITICAL"),
            ("THEFT", "HIGH"),
            ("ROBBERY", "HIGH"),
            ("VEHICLE THEFT", "MEDIUM"),
        ]
    )
    return {
        "event_id": f"TRAIN-LIVE-{time.time_ns()}",
        "timestamp": datetime.utcnow(),
        "status": "TRAINING EXERCISE",
        "priority": priority,
        "incident_type": incident_type,
        "latitude": latitude + random.uniform(-0.002, 0.002),
        "longitude": longitude + random.uniform(-0.002, 0.002),
        "address": area,
        "is_demo": True,
        "is_stream": True,
    }


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
    return "TRAINING EXERCISE · AVAILABLE" if is_training_status(status) else str(status)


API_URL = os.getenv("CRIME_RADAR_API_URL", "http://127.0.0.1:8000").rstrip("/")
REFRESH_SECONDS = 5
TRAINING_CASE_INTERVAL_SECONDS = 10
MAX_TRAINING_MAP_CASES = 20
DEMO_INCIDENTS = [
    {
        "event_id": "TRAIN-TN-001",
        "timestamp": datetime.utcnow(),
        "status": "TRAINING EXERCISE",
        "priority": "CRITICAL",
        "incident_type": "BURGLARY",
        "latitude": 13.0418,
        "longitude": 80.2337,
        "address": "T. Nagar, Chennai",
        "is_demo": True,
    },
    {
        "event_id": "TRAIN-TN-002",
        "timestamp": datetime.utcnow(),
        "status": "TRAINING EXERCISE",
        "priority": "HIGH",
        "incident_type": "THEFT",
        "latitude": 13.0604,
        "longitude": 80.2496,
        "address": "Egmore, Chennai",
        "is_demo": True,
    },
    {
        "event_id": "TRAIN-TN-003",
        "timestamp": datetime.utcnow(),
        "status": "TRAINING EXERCISE",
        "priority": "CRITICAL",
        "incident_type": "ASSAULT",
        "latitude": 13.0337,
        "longitude": 80.2702,
        "address": "Mylapore, Chennai",
        "is_demo": True,
    },
    {
        "event_id": "TRAIN-TN-004",
        "timestamp": datetime.utcnow(),
        "status": "TRAINING EXERCISE",
        "priority": "MEDIUM",
        "incident_type": "VEHICLE THEFT",
        "latitude": 13.0850,
        "longitude": 80.2101,
        "address": "Anna Nagar, Chennai",
        "is_demo": True,
    },
    {
        "event_id": "TRAIN-TN-005",
        "timestamp": datetime.utcnow(),
        "status": "TRAINING EXERCISE",
        "priority": "HIGH",
        "incident_type": "ROBBERY",
        "latitude": 13.0108,
        "longitude": 80.2150,
        "address": "Guindy, Chennai",
        "is_demo": True,
    },
    {
        "event_id": "TRAIN-TN-006",
        "timestamp": datetime.utcnow(),
        "status": "TRAINING EXERCISE",
        "priority": "CRITICAL",
        "incident_type": "BURGLARY",
        "latitude": 13.1143,
        "longitude": 80.2326,
        "address": "Perambur, Chennai",
        "is_demo": True,
    },
    {
        "event_id": "TRAIN-TN-007",
        "timestamp": datetime.utcnow(),
        "status": "TRAINING EXERCISE",
        "priority": "MEDIUM",
        "incident_type": "THEFT",
        "latitude": 13.0475,
        "longitude": 80.2824,
        "address": "Marina Beach, Chennai",
        "is_demo": True,
    },
    {
        "event_id": "TRAIN-TN-008",
        "timestamp": datetime.utcnow(),
        "status": "TRAINING EXERCISE",
        "priority": "HIGH",
        "incident_type": "ROBBERY",
        "latitude": 13.0067,
        "longitude": 80.2206,
        "address": "Little Mount, Chennai",
        "is_demo": True,
    },
]
DEMO_PATROL_UNITS = [
    {
        "unit_id": "SIM-TN-UNIT-201",
        "status": "TRAINING EXERCISE · AVAILABLE",
        "latitude": 13.0460,
        "longitude": 80.2290,
    },
    {
        "unit_id": "SIM-TN-UNIT-202",
        "status": "TRAINING EXERCISE · AVAILABLE",
        "latitude": 13.0370,
        "longitude": 80.2680,
    },
    {
        "unit_id": "SIM-TN-UNIT-203",
        "status": "TRAINING EXERCISE · AVAILABLE",
        "latitude": 13.1120,
        "longitude": 80.2320,
    },
]


def get_json(path, params=None):
    response = requests.get(f"{API_URL}{path}", params=params, timeout=5)
    response.raise_for_status()
    return response.json()


async def load_dashboard_data():
    incidents = await asyncio.to_thread(get_json, "/api/v1/active-incidents")
    units, sources = await asyncio.gather(
        asyncio.to_thread(get_json, "/api/v1/patrol-units"),
        asyncio.to_thread(get_json, "/api/v1/sources"),
    )
    return incidents, units, sources[0]


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

try:
    incidents_data, units_data, source_status = asyncio.run(load_dashboard_data())
    data_error = None
except (requests.RequestException, ValueError, KeyError) as exc:
    incidents_data, units_data = [], []
    data_error = str(exc)

if data_error:
    st.error(f"Telemetry service unavailable: {data_error}")
else:
    left_panel, map_panel, right_panel = st.columns([1.3, 3.4, 1.3], gap="small")
    with left_panel:
        st.markdown('<div class="radar-panel"><h3>Predictive Analytics</h3></div>', unsafe_allow_html=True)
        show_demo_data = st.toggle(
            "Show training exercise",
            value=True,
            help="Training exercise locations are not live incidents or police positions.",
        )

    incoming_df = pd.DataFrame(incidents_data)
    if not incoming_df.empty:
        incoming_df["is_demo"] = incoming_df["status"].map(is_training_status)
        incoming_df["is_stream"] = False
        agency_df = incoming_df[~incoming_df["is_demo"]].copy()
        persisted_demo_df = incoming_df[incoming_df["is_demo"]].copy()
    else:
        agency_df = incoming_df
        persisted_demo_df = incoming_df
    training_stream_incidents = st.session_state.setdefault(
        "training_stream_incidents", []
    )
    for incident in training_stream_incidents:
        incident["is_demo"] = True
        incident["is_stream"] = True
    training_clock = time.monotonic()
    if show_demo_data:
        last_training_case_at = st.session_state.setdefault(
            "last_training_case_at", training_clock
        )
        if training_clock - last_training_case_at >= TRAINING_CASE_INTERVAL_SECONDS:
            training_stream_incidents.append(create_training_incident())
            del training_stream_incidents[:-MAX_TRAINING_MAP_CASES]
            st.session_state["last_training_case_at"] = training_clock
            last_training_case_at = training_clock
    else:
        st.session_state["last_training_case_at"] = training_clock

    demo_df = (
        pd.concat(
            [
                persisted_demo_df,
                pd.DataFrame(DEMO_INCIDENTS),
                pd.DataFrame(training_stream_incidents),
            ],
            ignore_index=True,
        )
        if show_demo_data
        else pd.DataFrame()
    )
    df = pd.concat([agency_df, demo_df], ignore_index=True)
    if not df.empty:
        if "is_stream" not in df:
            df["is_stream"] = False
        else:
            df["is_stream"] = df["is_stream"].fillna(False).astype(bool)
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
    current_critical = (
        df.loc[df["priority"].astype("string").str.upper().eq("CRITICAL")]
        .to_dict(orient="records")
        if not df.empty
        else []
    )
    current_critical_ids = {str(incident["event_id"]) for incident in current_critical}
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
        prefix = "TRAINING EXERCISE · " if is_training else ""
        st.toast(
            f"{prefix}{incident_type} near {address}",
            icon="🚨",
        )

    simulated_units = [
        unit
        for unit in units_data
        if is_training_status(unit["status"])
    ]
    if show_demo_data and not simulated_units:
        simulated_units = DEMO_PATROL_UNITS

    demo_critical_incidents = (
        df.loc[df["is_demo"] & df["priority"].astype("string").str.upper().eq("CRITICAL")]
        .to_dict(orient="records")
        if not df.empty
        else []
    )
    unit_by_id = {str(unit["unit_id"]): unit for unit in simulated_units}
    existing_assignments = st.session_state.get("demo_dispatch_assignments", {})
    active_incident_ids = {str(incident["event_id"]) for incident in demo_critical_incidents}
    assignments = {}
    used_unit_ids = set()
    for event_id, unit_id in existing_assignments.items():
        event_id = str(event_id)
        unit_id = str(unit_id)
        if (
            event_id in active_incident_ids
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
    candidate_assignments = sorted(
        (
            haversine(
                unit["longitude"],
                unit["latitude"],
                incident["longitude"],
                incident["latitude"],
            ),
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
    for incident in demo_critical_incidents:
        event_id = str(incident["event_id"])
        unit_id = assignments.get(event_id)
        if unit_id is not None:
            unit = unit_by_id[unit_id]
            dispatch_by_incident[event_id] = {
                "unit": unit,
                "distance_km": haversine(
                    unit["longitude"],
                    unit["latitude"],
                    incident["longitude"],
                    incident["latitude"],
                ),
            }

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
            st.toast(
                f"TRAINING EXERCISE — {incident_type} near {incident_address}. "
                f"{dispatch['unit']['unit_id']} assigned "
                f"({dispatch['distance_km']:.2f} km). No police contacted.",
                icon="🚨",
            )
        notified_incidents.update(newly_assigned)

    incident_types = sorted(df["incident_type"].dropna().unique()) if not df.empty else []
    priorities = sorted(df["priority"].dropna().unique()) if not df.empty else []

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
        alert_level = "TRAINING ALERT" if demo_critical_count else (
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
            f"**{'Training event' if demo_count and not real_count else 'Latest report'}:** "
            f"{html.escape(latest_incident_type)}"
        )
        st.caption(
            f"Agency: {real_count} · Training: {demo_count} · "
            f"Critical: {critical_count} · Auto-refresh active"
        )
        if show_demo_data:
            st.caption(
                f"New training cases appear periodically · "
                f"up to {MAX_TRAINING_MAP_CASES} generated cases retained"
            )
        st.markdown('<div class="radar-panel"><h3>Filters & Layers</h3></div>', unsafe_allow_html=True)
        selected_type = st.selectbox("Incident type", ["All types", *incident_types])
        selected_priority = st.selectbox("Priority", ["All priorities", *priorities])
        show_patrol_units = st.checkbox("Show patrol telemetry", value=True)

    filtered_df = df.copy()
    if selected_type != "All types":
        filtered_df = filtered_df[filtered_df["incident_type"] == selected_type]
    if selected_priority != "All priorities":
        filtered_df = filtered_df[filtered_df["priority"] == selected_priority]

    focused_event_id = st.session_state.get("map_focus_event_id")
    map_df = filtered_df.copy()
    if focused_event_id is not None and not df.empty:
        focused_rows = df[df["event_id"].astype(str) == str(focused_event_id)]
        if not focused_rows.empty:
            if not map_df["event_id"].astype(str).eq(str(focused_event_id)).any():
                map_df = pd.concat([map_df, focused_rows], ignore_index=True)

    training_map_events = (
        map_df[map_df["is_stream"].astype(bool)].to_dict(orient="records")
        if not map_df.empty and "is_stream" in map_df
        else []
    )
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
        dispatch_detail = (
            f"<br><b>TRAINING PATROL ASSIGNED:</b> "
            f"{html.escape(str(dispatch['unit']['unit_id']))}"
            f" · {dispatch['distance_km']:.2f} km away"
            if dispatch
            else ""
        )
        training_map_payload.append(
            {
                "event_id": event_id,
                "latitude": float(incident["latitude"]),
                "longitude": float(incident["longitude"]),
                "priority": str(incident["priority"]).upper(),
                "popup_html": (
                    f"<b>TRAINING EXERCISE · {incident_type}</b><br>"
                    f"{incident_address}<br>"
                    f"{html.escape(str(incident['priority']))} · "
                    f"{html.escape(display_incident_status(incident['status']))}"
                    f"{dispatch_detail}<br>Example location · not a live report"
                ),
            }
        )
    render_map_df = (
        map_df[~map_df["is_stream"].astype(bool)]
        if not map_df.empty and "is_stream" in map_df
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
                f'<div class="radar-live-alert">TRAINING EXERCISE: {demo_critical_count} '
                f'critical event{"s" if demo_critical_count != 1 else ""}</div>',
                unsafe_allow_html=True,
            )
        st.markdown('<div class="radar-panel"><h3>Map Legend</h3><small>State outline · Tamil Nadu jurisdiction<br>'
                    'Cyan heatmap · incident density<br>'
                    'Red dot · selected incident<br>'
                    'Red ring · critical report<br>'
                    'Cyan line · received patrol telemetry only<br>'
                    'Blue marker · received patrol asset</small></div>', unsafe_allow_html=True)

    with map_panel:
        toolbar_city, toolbar_scope = st.columns([2, 1])
        with toolbar_city:
            city_centers = {
                "Chennai": [13.0827, 80.2707],
                "Coimbatore": [11.0168, 76.9558],
                "Madurai": [9.9252, 78.1198],
                "Salem": [11.6643, 78.1460],
                "Tiruchirappalli": [10.7905, 78.7047],
                "Tirunelveli": [8.7139, 77.7567],
                "Erode": [11.3410, 77.7172],
            }
            map_city = st.selectbox(
                "Map focus", list(city_centers), label_visibility="collapsed", key="map-city-focus"
            )
        with toolbar_scope:
            status_text = (
                f"TRAINING EXERCISE · {demo_count} SAMPLE EVENTS · NOT LIVE"
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
                f'<div class="radar-live-alert">⚠ TRAINING EXERCISE · '
                f'{demo_critical_count} critical events · NOT LIVE</div>',
                unsafe_allow_html=True,
            )
        elif not filtered_df.empty:
            st.markdown(
                f'<div class="radar-live-alert">● {len(filtered_df)} matching report(s) · '
                'training events are clearly marked</div>',
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
            zoom_start=15,
            min_zoom=7,
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
        if not render_map_df.empty:
            incident_heat_layer = HeatMap(
                render_map_df[["latitude", "longitude"]].values.tolist(),
                name="Incident density",
                radius=24,
                blur=20,
                min_opacity=0.35,
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
                if dispatch:
                    dispatch_detail = (
                        f"<br><b>TRAINING PATROL ASSIGNED:</b> "
                        f"{html.escape(str(dispatch['unit']['unit_id']))}"
                        f" · {dispatch['distance_km']:.2f} km away"
                    )
                if is_critical:
                    folium.Circle(
                        location=incident_location,
                        radius=1500,
                        color="#ff354d",
                        weight=2,
                        fill=True,
                        fill_color="#ff354d",
                        fill_opacity=0.1,
                        tooltip="Critical containment zone · 1.50 km radius",
                    ).add_to(radar_map)
                incident_marker = folium.CircleMarker(
                    location=incident_location,
                    radius=7,
                    color="#22bad1",
                    weight=2,
                    fill=True,
                    fill_color="#22bad1",
                    fill_opacity=0.95,
                    tooltip=(
                        f"{'TRAINING · ' if is_demo else ''}{incident_name} · "
                        f"{incident['priority']}"
                    ),
                    popup=folium.Popup(
                        f"<b>{'TRAINING EXERCISE · ' if is_demo else ''}{incident_name}</b><br>"
                        f"{incident_address}<br>"
                        f"{html.escape(str(incident['priority']))} · "
                        f"{html.escape(display_incident_status(incident['status']))}"
                        f"{dispatch_detail}",
                        max_width=280,
                        show=False,
                    ),
                ).add_to(radar_map)
                IncidentMarkerRegistry(incident_id, incident_marker).add_to(radar_map)
        elif show_demo_data:
            incident_heat_layer = HeatMap(
                [],
                name="Incident density",
                radius=24,
                blur=20,
                min_opacity=0.35,
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
                    f"{'TRAINING EXERCISE · ' if is_demo_unit else ''}"
                    f"{html.escape(unit['unit_id'])}<br>"
                    f"Status: {html.escape(display_patrol_status(unit_status))}"
                )
                unit_tooltip = (
                    f"{'TRAINING · ' if is_demo_unit else ''}{html.escape(unit['unit_id'])}"
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
                        tooltip=f"TRAINING ASSIGNMENT · {distance:.2f} km · NOT LIVE",
                    ).add_to(radar_map)
                    unit_popup += (
                        f"<br>TRAINING ASSIGNED TO: {html.escape(assigned_to)}"
                        f"<br>Distance: {distance:.2f} km"
                    )
                    unit_tooltip += f" · TRAINING ASSIGNED · {distance:.1f} km"
                patrol_marker = folium.Marker(
                    location=unit_location,
                    popup=unit_popup,
                    tooltip=unit_tooltip,
                    icon=folium.Icon(color="blue", icon="shield", prefix="fa"),
                ).add_to(radar_map)

        if show_patrol_units and displayed_units:
            st.caption(
                f"Displaying {len(displayed_units)} patrol marker(s). "
                "Training units are examples and do not represent live police positions."
            )
        elif show_patrol_units:
            st.caption("No recent patrol telemetry received.")
        components.html(
            training_map_sync_html(training_map_payload),
            height=0,
            scrolling=False,
        )
        MapFocusWatcher(map_city, show_demo_data).add_to(radar_map)

        map_signature = json.dumps(
            {
                "map_mode": "incremental-training-v5",
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
                "show_training": show_demo_data,
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
        st.caption(
            "TRAINING EXERCISE · NOT LIVE" if show_demo_data else "Alerts hidden."
        )
        if show_demo_data:
            if not demo_critical_incidents:
                st.caption("No critical training incidents to dispatch.")
            elif not simulated_units:
                st.warning("No training patrol units are available for assignment.")
            else:
                for incident in demo_critical_incidents:
                    incident_id = str(incident["event_id"])
                    dispatch = dispatch_by_incident.get(incident_id)
                    incident_type = display_incident_type(incident["incident_type"], True)
                    incident_address = display_incident_address(incident["address"], True)
                    st.button(
                        f"🚨 {incident_type} · {incident_address}",
                        key=f"locate-incident-{incident_id}",
                        use_container_width=True,
                        on_click=focus_incident_on_map,
                        args=(incident_id,),
                        type="primary",
                    )
                    if dispatch:
                        st.caption(
                            f"Assigned: {dispatch['unit']['unit_id']} · "
                            f"{dispatch['distance_km']:.2f} km away · "
                            "training assignment only"
                        )
                    else:
                        st.caption("No training patrol available for assignment.")
        st.markdown('<div class="radar-panel"><h3>Incident Frequency</h3></div>', unsafe_allow_html=True)
        if filtered_df.empty:
            st.caption("No incidents to chart.")
        else:
            chart_data = filtered_df.assign(
                category=filtered_df["priority"].str.upper().eq("CRITICAL").map(
                    {True: "Critical", False: "Other"}
                )
            ).pivot_table(
                index="incident_type",
                columns="category",
                values="event_id",
                aggfunc="count",
                fill_value=0,
            )
            for category in ("Critical", "Other"):
                if category not in chart_data:
                    chart_data[category] = 0
            st.bar_chart(
                chart_data[["Other", "Critical"]],
                color=["#27c5db", "#ff5364"],
                height=250,
            )
        st.markdown('<div class="radar-panel"><h3>Filtered Incidents</h3></div>', unsafe_allow_html=True)
        if filtered_df.empty:
            st.caption("No matching incident clusters.")
        else:
            for _, incident in filtered_df.head(6).iterrows():
                critical_class = " radar-zone-critical" if str(incident["priority"]).upper() == "CRITICAL" else ""
                demo_label = " · TRAINING EXERCISE" if bool(incident["is_demo"]) else ""
                incident_type = display_incident_type(
                    incident["incident_type"], bool(incident["is_demo"])
                )
                incident_address = display_incident_address(
                    incident["address"], bool(incident["is_demo"])
                )
                st.markdown(
                    f'<div class="radar-zone{critical_class}">'
                    f'<b>{html.escape(incident_type)}</b><br>'
                    f'<small>{html.escape(incident_address)}</small><br>'
                    f'<small>{html.escape(str(incident["priority"]))} · '
                    f'{html.escape(display_incident_status(incident["status"]))}'
                    f'{demo_label}</small></div>',
                    unsafe_allow_html=True,
                )

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
        telemetry_table.insert(
            0,
            "source",
            filtered_df["is_demo"].map(
                {True: "TRAINING EXERCISE · NOT LIVE", False: "AGENCY"}
            ),
        )
        st.dataframe(
            telemetry_table,
            use_container_width=True,
            hide_index=True,
        )


time.sleep(REFRESH_SECONDS)
st.rerun()
