from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="Cairo PM2.5 Forecast Dashboard",
    page_icon="🌫️",
    layout="wide",
)

# District coordinates matching the forecast pipeline
DISTRICT_COORDS = {
    "downtown": {"lat": 30.0444, "lon": 31.2357, "label": "Downtown"},
    "helwan": {"lat": 29.8490, "lon": 31.3342, "label": "Helwan"},
    "shobra": {"lat": 30.1286, "lon": 31.2422, "label": "Shobra"},
    "new_cairo": {"lat": 30.0074, "lon": 31.4913, "label": "New Cairo"},
    "october": {"lat": 29.9723, "lon": 30.9458, "label": "6th of October"},
}

# Colors for the 6 EPA AQI categories used in the pipeline
AQI_COLORS = {
    "Good": "#2ecc71",
    "Moderate": "#f1c40f",
    "Unhealthy for Sensitive Groups": "#e67e22",
    "Unhealthy": "#e74c3c",
    "Very Unhealthy": "#8e44ad",
    "Hazardous": "#7f0000",
}

# Plain-language health guidance per AQI category
HEALTH_ADVICE = {
    "Good": (
        "Air quality is clean and safe. Normal outdoor activities are fine "
        "for everyone."
    ),
    "Moderate": (
        "Air quality is acceptable. Unusually sensitive people should consider "
        "limiting prolonged outdoor exertion."
    ),
    "Unhealthy for Sensitive Groups": (
        "Sensitive groups (children, older adults, and people with asthma or "
        "heart/lung conditions) should reduce prolonged outdoor exertion."
    ),
    "Unhealthy": (
        "Everyone may begin to experience health effects. Limit prolonged "
        "outdoor exertion and consider wearing a mask outdoors."
    ),
    "Very Unhealthy": (
        "Health alert: risk of serious effects increases for everyone. Avoid "
        "outdoor physical activity and keep indoor air clean."
    ),
    "Hazardous": (
        "Emergency conditions: stay indoors with windows closed and avoid all "
        "outdoor exertion."
    ),
}

HORIZON_LABELS = {
    1: "24h (Horizon 1 - Tomorrow)",
    2: "48h (Horizon 2 - Day 2)",
    3: "72h (Horizon 3 - Day 3)",
}


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convert a hex color string to an rgba() string with given alpha."""
    hex_clean = hex_color.lstrip("#")
    r = int(hex_clean[0:2], 16)
    g = int(hex_clean[2:4], 16)
    b = int(hex_clean[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


@st.cache_data
def load_forecasts(csv_path: str) -> pd.DataFrame:
    """Load forecast data and attach district map coordinates."""
    df = pd.read_csv(csv_path)
    df["horizon"] = df["horizon"].astype(int)
    df["lat"] = df["station"].map(lambda s: DISTRICT_COORDS.get(s, {}).get("lat"))
    df["lon"] = df["station"].map(lambda s: DISTRICT_COORDS.get(s, {}).get("lon"))
    df["district_name"] = df["station"].map(
        lambda s: DISTRICT_COORDS.get(s, {}).get("label", s)
    )
    return df


def _extract_clicked_station(
    map_key: str, valid_stations: list[str]
) -> str | None:
    """Return station code if the user clicked a marker on the map chart."""
    event_state = st.session_state.get(map_key)
    if not event_state:
        return None

    selection = getattr(event_state, "selection", None)
    if selection is None and isinstance(event_state, dict):
        selection = event_state.get("selection")
    if not selection:
        return None

    points = getattr(selection, "points", None)
    if points is None and isinstance(selection, dict):
        points = selection.get("points")
    if not points:
        return None

    first_pt = points[0]
    custom_data = getattr(first_pt, "customdata", None)
    if custom_data is None and isinstance(first_pt, dict):
        custom_data = first_pt.get("customdata")

    if isinstance(custom_data, (list, tuple)) and custom_data:
        station = str(custom_data[0])
    elif custom_data is not None:
        station = str(custom_data)
    else:
        return None

    return station if station in valid_stations else None


def render_district_map(
    horizon_df: pd.DataFrame, selected_station: str
) -> None:
    """Render static Greater Cairo map with click-to-select district markers."""
    fig = go.Figure()

    for _, row in horizon_df.iterrows():
        station_code = str(row["station"])
        is_selected = station_code == selected_station
        base_color = AQI_COLORS.get(str(row["aqi_category"]), "#e67e22")
        marker_color = (
            base_color if is_selected else _hex_to_rgba(base_color, 0.65)
        )
        marker_size = 21 if is_selected else 13

        hover_text = (
            f"<b>{row['district_name']}</b> ({station_code})<br>"
            f"AQI: {row['aqi_category']}<br>"
            f"PM2.5: {row['pred_pm25']} ug/m3<br>"
            f"Risk Prob: {float(row['risk_prob']):.1%}<br>"
            f"Alert: {row['alert_level']}<br>"
            f"<i>Click marker to select district</i>"
        )

        if hasattr(go, "Scattermap"):
            fig.add_trace(
                go.Scattermap(
                    lat=[row["lat"]],
                    lon=[row["lon"]],
                    mode="markers+text",
                    name=str(row["aqi_category"]),
                    showlegend=False,
                    text=[row["district_name"]],
                    textposition="top center",
                    customdata=[station_code],
                    hovertext=[hover_text],
                    hoverinfo="text",
                    marker=dict(size=marker_size, color=marker_color),
                )
            )
        elif hasattr(go, "Scattermapbox"):
            fig.add_trace(
                go.Scattermapbox(
                    lat=[row["lat"]],
                    lon=[row["lon"]],
                    mode="markers+text",
                    name=str(row["aqi_category"]),
                    showlegend=False,
                    text=[row["district_name"]],
                    textposition="top center",
                    customdata=[station_code],
                    hovertext=[hover_text],
                    hoverinfo="text",
                    marker=dict(size=marker_size, color=marker_color),
                )
            )
        else:
            fig.add_trace(
                go.Scatter(
                    x=[row["lon"]],
                    y=[row["lat"]],
                    mode="markers+text",
                    name=str(row["aqi_category"]),
                    showlegend=False,
                    text=[row["district_name"]],
                    textposition="top center",
                    customdata=[station_code],
                    hovertext=[hover_text],
                    hoverinfo="text",
                    marker=dict(size=marker_size, color=marker_color),
                )
            )

    if hasattr(go, "Scattermap"):
        fig.update_layout(
            map=dict(
                style="open-street-map",
                center=dict(lat=30.01, lon=31.24),
                zoom=9.5,
            ),
            clickmode="event+select",
            height=430,
            margin=dict(l=0, r=0, t=10, b=0),
        )
    elif hasattr(go, "Scattermapbox"):
        fig.update_layout(
            mapbox=dict(
                style="open-street-map",
                center=dict(lat=30.01, lon=31.24),
                zoom=9.5,
            ),
            clickmode="event+select",
            height=430,
            margin=dict(l=0, r=0, t=10, b=0),
        )
    else:
        fig.update_layout(
            xaxis_title="Longitude",
            yaxis_title="Latitude",
            clickmode="event+select",
            height=430,
            margin=dict(l=10, r=10, t=20, b=10),
        )

    st.plotly_chart(
        fig,
        use_container_width=True,
        key="cairo_district_map",
        on_select="rerun",
        selection_mode="points",
    )


def render_aqi_legend() -> None:
    """Display a clean color legend for all 6 AQI categories."""
    cols = st.columns(3)
    items = list(AQI_COLORS.items())
    for idx, (category, color) in enumerate(items):
        with cols[idx % 3]:
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:8px;margin:3px 0;'>"
                f"<span style='display:inline-block;width:13px;height:13px;"
                f"border-radius:50%;background:{color};flex-shrink:0;'></span>"
                f"<span style='font-size:12px;'>{category}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )


def main() -> None:
    st.title("Cairo Air Quality Forecast Dashboard (PM2.5)")
    st.caption(
        "Forecasts for 5 Greater Cairo districts across 24h, 48h, and 72h horizons."
    )

    here = Path(__file__).resolve().parent
    csv_file = here.parent / "pipeline" / "forecasts.csv"
    if not csv_file.exists():
        st.error("forecasts.csv was not found in src/pipeline. Run the pipeline first.")
        return

    df = load_forecasts(str(csv_file))

    # Sidebar controls: horizon switcher and district selector
    st.sidebar.header("Forecast Controls")
    selected_horizon = st.sidebar.radio(
        "Select Forecast Horizon",
        options=[1, 2, 3],
        format_func=lambda h: HORIZON_LABELS[h],
    )

    horizon_df = df[df["horizon"] == selected_horizon].copy()
    if horizon_df.empty:
        st.warning("No forecast rows available for the selected horizon.")
        return

    station_options = horizon_df["station"].tolist()
    if (
        "selected_district" not in st.session_state
        or st.session_state["selected_district"] not in station_options
    ):
        st.session_state["selected_district"] = station_options[0]

    # Sync map marker click into selected_district before rendering selectbox
    clicked_station = _extract_clicked_station("cairo_district_map", station_options)
    if clicked_station and clicked_station != st.session_state["selected_district"]:
        st.session_state["selected_district"] = clicked_station

    selected_station = st.sidebar.selectbox(
        "Select District",
        options=station_options,
        key="selected_district",
        format_func=lambda s: DISTRICT_COORDS.get(s, {}).get("label", s),
    )

    row = horizon_df[horizon_df["station"] == selected_station].iloc[0]

    # Top summary metrics for the selected district and horizon
    issue_str = str(row["issue_date"])
    target_str = str(row["target_date"])
    st.info(
        f"Issue Date: {issue_str} | Target Date: {target_str} | "
        f"Horizon: {HORIZON_LABELS[selected_horizon]}"
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Predicted PM2.5", f"{row['pred_pm25']} ug/m3")
    col2.metric("Health-Risk Probability (>55.4)", f"{float(row['risk_prob']):.1%}")
    col3.metric("Alert Level", str(row["alert_level"]).upper())

    # Main layout: static clickable map on left, advice and reliability note on right
    map_col, info_col = st.columns([1.4, 1])

    with map_col:
        st.subheader("Districts Map by Predicted AQI Category")
        render_district_map(horizon_df, selected_station)
        render_aqi_legend()
        st.caption("Tip: Click any district marker on the map to inspect it.")

    with info_col:
        st.subheader(f"Health Advice: {row['district_name']}")
        aqi_cat = str(row["aqi_category"])
        st.markdown(f"**AQI Category:** `{aqi_cat}`")
        st.write(HEALTH_ADVICE.get(aqi_cat, "Follow local air quality guidance."))

        st.subheader("Alert & Reliability Note")
        alert_lvl = str(row["alert_level"]).lower()
        if alert_lvl == "high":
            st.error("Alert Level: HIGH - Elevated health risk expected.")
        elif alert_lvl == "warning":
            st.warning("Alert Level: WARNING - Moderate-to-elevated risk signal.")
        else:
            st.success("Alert Level: NONE - No active threshold alert.")

        # Placeholder until Task 2 provides final recall and false-alarm numbers
        st.caption(
            "Reliability Note: 48h and 72h alerts have lower recall (they can miss "
            "risky days, especially in spring and summer). Final recall and false "
            "alarms per 100 days from Task 2 will be updated here."
        )

    with st.expander("View All Districts Table for Selected Horizon"):
        display_cols = [
            "district_name",
            "station",
            "target_date",
            "pred_pm25",
            "risk_prob",
            "alert_level",
            "aqi_category",
        ]
        st.dataframe(
            horizon_df[display_cols], use_container_width=True, hide_index=True
        )


if __name__ == "__main__":
    main()