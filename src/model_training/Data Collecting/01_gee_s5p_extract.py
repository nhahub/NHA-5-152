"""
Step 1: Extract Sentinel-5P TROPOMI column densities (NO2, SO2, CO, O3)
for the 5 Greater Cairo stations.

Run:
    pip install earthengine-api pandas
    earthengine authenticate
    python 01_gee_s5p_extract.py

Output: s5p_raw_long.csv  ->  columns: station_id, date, pollutant, value_mol_m2, n_pixels
"""

import time
import ee
import pandas as pd

# ---------------------------------------------------------------- config
EE_PROJECT = "air-quality-cairo"
START, END = "2023-09-01", "2026-09-01"
BUFFER_M = 3000          # also re-run with 7000 as a sensitivity check
SCALE = 1113             # ~native 0.01 deg grid of the S5P L3 product
OUT = "s5p_raw_long.csv"

PRODUCTS = {
    "NO2": ("COPERNICUS/S5P/OFFL/L3_NO2", "tropospheric_NO2_column_number_density"),
    "SO2": ("COPERNICUS/S5P/OFFL/L3_SO2", "SO2_column_number_density"),
    "CO":  ("COPERNICUS/S5P/OFFL/L3_CO",  "CO_column_number_density"),
    "O3":  ("COPERNICUS/S5P/OFFL/L3_O3",  "O3_column_number_density"),
}

ee.Initialize(project=EE_PROJECT)

# ---------------------------------------------------------------- stations
stations = pd.read_csv("stations.csv")

buffers = ee.FeatureCollection([
    ee.Feature(
        ee.Geometry.Point([float(r.lon), float(r.lat)]).buffer(BUFFER_M),
        {"station_id": r.station_id},
    )
    for r in stations.itertuples()
])


# ---------------------------------------------------------------- extraction
def chunk(collection_id, band, pollutant, date_start, date_end):
    """Return a list of dict rows for one pollutant over one date window."""
    col = (ee.ImageCollection(collection_id)
           .select(band)
           .filterDate(date_start, date_end)
           .filterBounds(buffers))

    def per_image(img):
        d = img.date().format("YYYY-MM-dd")
        return img.reduceRegions(
            collection=buffers,
            reducer=ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True),
            scale=SCALE,
        ).map(lambda f: f.set({"date": d, "pollutant": pollutant}))

    fc = col.map(per_image).flatten()
    fc = fc.select(["station_id", "date", "pollutant", "mean", "count"], None, False)

    info = fc.getInfo()["features"]
    rows = []
    for f in info:
        p = f["properties"]
        if p.get("mean") is None:      # fully masked (cloud / bad QA) -> skip
            continue
        rows.append({
            "station_id": p["station_id"],
            "date": p["date"],
            "pollutant": p["pollutant"],
            "value_mol_m2": p["mean"],
            "n_pixels": p.get("count"),
        })
    return rows


# Year-by-year, pollutant-by-pollutant. Do NOT try the whole range at once:
# a single getInfo() over 3 years x 4 pollutants will time out server-side.
windows = [("2023-09-01", "2024-01-01"),
           ("2024-01-01", "2025-01-01"),
           ("2025-01-01", "2026-01-01"),
           ("2026-01-01", "2026-09-01")]

all_rows = []
for pollutant, (cid, band) in PRODUCTS.items():
    for ws, we in windows:
        for attempt in range(3):
            try:
                r = chunk(cid, band, pollutant, ws, we)
                all_rows.extend(r)
                print(f"{pollutant} {ws}..{we}: {len(r)} rows")
                break
            except Exception as e:
                print(f"  retry {attempt+1} for {pollutant} {ws}: {e}")
                time.sleep(20)
        else:
            print(f"!! FAILED {pollutant} {ws}..{we} -- split this window further "
                  f"or use Export.table.toDrive (see bottom of file)")

df = pd.DataFrame(all_rows)
df.to_csv(OUT, index=False)
print(f"\nwrote {OUT}: {df.shape}")

# ---------------------------------------------------------------- coverage report
if not df.empty:
    cov = (df.groupby(["pollutant", "station_id"])["date"].nunique()
             .unstack(fill_value=0))
    total_days = (pd.Timestamp(END) - pd.Timestamp(START)).days
    print(f"\ndays with a valid observation (out of {total_days}):")
    print(cov)
    print("\ncoverage %:")
    print((cov / total_days * 100).round(1))
    print("\ndate range per pollutant:")
    print(df.groupby("pollutant")["date"].agg(["min", "max"]))


# ---------------------------------------------------------------- fallback
# If getInfo() keeps timing out, export to Drive instead and download manually:
#
# task = ee.batch.Export.table.toDrive(
#     collection=fc, description="s5p_no2_2024", fileFormat="CSV")
# task.start()
# while task.active():
#     print(task.status()["state"]); time.sleep(30)
