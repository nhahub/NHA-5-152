"""
Step 2: Build static spatial features per station from OpenStreetMap:
road network metrics + industrial zone metrics.

Run:
    pip install osmnx geopandas pandas shapely
    python 02_osmnx_spatial_features.py

Outputs:
    spatial_features.csv          one row per station
    osm_debug/<station>_roads.geojson      <- OPEN THESE IN QGIS BEFORE TRUSTING THE NUMBERS
    osm_debug/<station>_industrial.geojson
"""

import os
import math
import warnings

import geopandas as gpd
import osmnx as ox
import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------- config
UTM = 32636               # UTM zone 36N -- correct for Cairo; metres, not degrees
RADII = [3000, 5000]      # buffers to compute features at (must match GEE buffer)
FAR = 10000               # wide search radius for "distance to nearest X"
DEBUG_DIR = "osm_debug"

ox.settings.use_cache = True
ox.settings.log_console = False

INDUSTRIAL_TAGS = {
    "landuse": ["industrial", "quarry"],
    "man_made": ["works", "kiln", "chimney"],
    "power": ["plant"],
    "building": ["industrial", "warehouse", "factory"],
}

MAJOR = "motorway|trunk|primary"

os.makedirs(DEBUG_DIR, exist_ok=True)
stations = pd.read_csv("stations.csv")


def circle_area_km2(r_m):
    return math.pi * r_m ** 2 / 1e6


def nearest_distance_m(gdf, point_proj):
    """Min distance in metres from the station to any geometry in gdf."""
    if gdf is None or gdf.empty:
        return float("nan")
    return float(gdf.distance(point_proj).min())


rows = []
for r in stations.itertuples():
    print(f"--- {r.station_id}")
    pt = (float(r.lat), float(r.lon))
    feat = {"station_id": r.station_id, "lat": r.lat, "lon": r.lon}

    station_pt = (gpd.GeoSeries([gpd.points_from_xy([r.lon], [r.lat])[0]],
                                crs="EPSG:4326").to_crs(epsg=UTM).iloc[0])

    # ---------------- road network, per radius
    for R in RADII:
        try:
            G = ox.graph_from_point(pt, dist=R, network_type="drive")
            Gp = ox.project_graph(G, to_crs=f"EPSG:{UTM}")
            edges = ox.graph_to_gdfs(Gp, nodes=False)
            stats = ox.basic_stats(Gp)

            area = circle_area_km2(R)
            total_km = edges.length.sum() / 1000
            hw = edges["highway"].astype(str)
            major_km = edges[hw.str.contains(MAJOR, na=False)].length.sum() / 1000

            feat[f"road_length_km_{R}"] = round(total_km, 3)
            feat[f"road_density_kmkm2_{R}"] = round(total_km / area, 3)
            feat[f"intersection_count_{R}"] = stats["intersection_count"]
            feat[f"intersection_density_{R}"] = round(stats["intersection_count"] / area, 3)
            feat[f"street_length_avg_{R}"] = round(stats["street_length_avg"], 2)
            feat[f"circuity_avg_{R}"] = round(stats["circuity_avg"], 4)
            feat[f"major_road_km_{R}"] = round(major_km, 3)
            feat[f"major_road_share_{R}"] = round(major_km / total_km, 4) if total_km else 0.0

            if R == RADII[0]:
                edges.reset_index()[["highway", "geometry"]].to_file(
                    f"{DEBUG_DIR}/{r.station_id}_roads.geojson", driver="GeoJSON")
        except Exception as e:
            print(f"    roads R={R} failed: {e}")

    # ---------------- distance to nearest major road (wide search)
    try:
        Gf = ox.graph_from_point(pt, dist=FAR, network_type="drive")
        Ef = ox.graph_to_gdfs(ox.project_graph(Gf, to_crs=f"EPSG:{UTM}"), nodes=False)
        mj = Ef[Ef["highway"].astype(str).str.contains(MAJOR, na=False)]
        feat["dist_to_major_road_m"] = round(nearest_distance_m(mj, station_pt), 1)
    except Exception as e:
        print(f"    major-road distance failed: {e}")
        feat["dist_to_major_road_m"] = float("nan")

    # ---------------- industrial zones, per radius
    for R in RADII:
        try:
            ind = ox.features_from_point(pt, tags=INDUSTRIAL_TAGS, dist=R)
            ind = ind.to_crs(epsg=UTM)
            polys = ind[ind.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
            # dissolve first: overlapping tags (landuse + building) double-count area
            area_km2 = (polys.union_all().area / 1e6) if len(polys) else 0.0
            feat[f"industrial_area_km2_{R}"] = round(area_km2, 4)
            feat[f"industrial_ratio_{R}"] = round(area_km2 / circle_area_km2(R), 5)
            feat[f"industrial_count_{R}"] = int(len(ind))
            if R == RADII[0] and len(ind):
                ind.reset_index()[["geometry"]].to_file(
                    f"{DEBUG_DIR}/{r.station_id}_industrial.geojson", driver="GeoJSON")
        except Exception as e:
            print(f"    industrial R={R}: {e}  -> recording as 0")
            feat[f"industrial_area_km2_{R}"] = 0.0
            feat[f"industrial_ratio_{R}"] = 0.0
            feat[f"industrial_count_{R}"] = 0

    # ---------------- distance to nearest industrial site (wide search)
    try:
        indf = ox.features_from_point(pt, tags=INDUSTRIAL_TAGS, dist=FAR).to_crs(epsg=UTM)
        feat["dist_to_industrial_m"] = round(nearest_distance_m(indf, station_pt), 1)
    except Exception as e:
        print(f"    industrial distance failed: {e}")
        feat["dist_to_industrial_m"] = float("nan")

    rows.append(feat)

out = pd.DataFrame(rows)
out.to_csv("spatial_features.csv", index=False)
print("\nwrote spatial_features.csv", out.shape)
print(out[["station_id", "road_density_kmkm2_3000",
           "industrial_ratio_3000", "dist_to_industrial_m"]].to_string(index=False))

print("""
SANITY CHECK -- do this before handing the file over:
  Helwan and Shobra El-Kheima MUST have higher industrial_ratio than New Cairo.
  If they don't, OSM landuse coverage for that area is incomplete (common in Egypt)
  and you need to say so explicitly rather than ship a misleading zero.
  Open the files in osm_debug/ in QGIS or geojson.io and look at them.
""")
