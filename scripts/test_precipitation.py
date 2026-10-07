from pathlib import Path
from datetime import date, timedelta
import csv
import io
import json
import math
import statistics

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
OUT = DATA / "precipitation_test.json"

API = "https://dataset.api.hub.geosphere.at/v1/timeseries/historical/spartacus-v3-1d-1km"
PARAMETER = "RR"
TEST_PLACES = ["Wien", "Reutte", "Bregenz", "Graz", "Salzburg"]
START = date(1961, 1, 1)
END = date.today()
TIMEOUT = 180


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def municipality_lookup():
    obj = load_json(DATA / "municipalities.json")
    cols = obj["columns"]
    ix = {name: i for i, name in enumerate(cols)}
    wanted = {}
    for row in obj["municipalities"]:
        name = row[ix["name"]]
        if name in TEST_PLACES and name not in wanted:
            wanted[name] = {
                "name": name,
                "state": row[ix["state"]],
                "lat": float(row[ix["lat"]]),
                "lon": float(row[ix["lon"]]),
            }
    missing = [name for name in TEST_PLACES if name not in wanted]
    if missing:
        raise RuntimeError(f"Testgemeinden fehlen in municipalities.json: {missing}")
    return [wanted[name] for name in TEST_PLACES]


def find_column(fieldnames, exact=(), contains=()):
    low = {str(name).lower(): name for name in fieldnames or [] if name is not None}
    for key in exact:
        if key.lower() in low:
            return low[key.lower()]
    for name in fieldnames or []:
        lname = str(name).lower()
        if any(token.lower() in lname for token in contains):
            return name
    return None


def parse_float(value):
    if value in (None, "", "nan", "NaN", "null", "None"):
        return None
    try:
        x = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def fetch_csv(places):
    params = [
        ("parameters", PARAMETER),
        ("start", START.isoformat()),
        ("end", END.isoformat()),
        ("output_format", "csv"),
    ]
    for p in places:
        params.append(("lat_lon", f'{p["lat"]},{p["lon"]}'))

    r = requests.get(API, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    print("GeoSphere datapoint-count:", r.headers.get("datapoint-count"))
    return r.text


def parse_csv(text, places):
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []

    time_col = find_column(fields, exact=("time", "date", "timestamp"), contains=("time", "date"))
    lat_col = find_column(fields, exact=("lat", "latitude"), contains=("latitude", "lat"))
    lon_col = find_column(fields, exact=("lon", "longitude"), contains=("longitude", "lon"))
    rr_col = find_column(fields, exact=("RR",), contains=("rr [", "rr[", "precip"))

    if not time_col or not rr_col:
        raise RuntimeError(f"CSV-Spalten unerwartet: {fields}")

    # If coordinates are present, assign each returned grid point to the nearest requested place.
    # If not, fall back to row order only when the API provides one series at a time; for this
    # five-place test we expect lat/lon columns to be present.
    if not lat_col or not lon_col:
        raise RuntimeError(f"Keine lat/lon-Spalten im Timeseries-CSV gefunden: {fields}")

    series = {p["name"]: {} for p in places}
    returned_coords = {p["name"]: set() for p in places}

    for row in reader:
        raw_time = row.get(time_col)
        rr = parse_float(row.get(rr_col))
        lat = parse_float(row.get(lat_col))
        lon = parse_float(row.get(lon_col))
        if not raw_time or rr is None or lat is None or lon is None:
            continue
        day = str(raw_time)[:10]

        nearest = min(
            places,
            key=lambda p: (p["lat"] - lat) ** 2 + (p["lon"] - lon) ** 2,
        )
        series[nearest["name"]][day] = rr
        returned_coords[nearest["name"]].add((round(lat, 6), round(lon, 6)))

    return series, returned_coords, fields


def percentile(values, q):
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    frac = pos - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac


def rolling_30(day_values):
    dates = sorted(date.fromisoformat(d) for d in day_values)
    if not dates:
        return {}
    start = dates[0]
    end = dates[-1]
    out = {}
    window = []
    running = 0.0
    d = start
    while d <= end:
        key = d.isoformat()
        value = day_values.get(key)
        window.append(value)
        if value is not None:
            running += value
        if len(window) > 30:
            old = window.pop(0)
            if old is not None:
                running -= old
        if len(window) == 30 and all(v is not None for v in window):
            out[key] = round(running, 1)
        d += timedelta(days=1)
    return out


def climatology(rolling, start_year, end_year):
    buckets = {}
    for day, value in rolling.items():
        y = int(day[:4])
        if start_year <= y <= end_year:
            buckets.setdefault(day[5:], []).append(value)

    out = {}
    for md, values in buckets.items():
        if len(values) < 20:
            continue
        out[md] = {
            "n": len(values),
            "p20": round(percentile(values, 0.20), 1),
            "median": round(statistics.median(values), 1),
            "p80": round(percentile(values, 0.80), 1),
        }
    return out


def category(value, ref):
    if value is None or not ref:
        return None
    if value < ref["p20"]:
        return "dry"
    if value > ref["p80"]:
        return "wet"
    return "normal"


def build_place_output(place, daily, returned_coords):
    rolling = rolling_30(daily)
    clim_old = climatology(rolling, 1961, 1990)
    clim_modern = climatology(rolling, 1991, 2020)

    latest_day = max(rolling) if rolling else None
    latest_value = rolling.get(latest_day) if latest_day else None
    md = latest_day[5:] if latest_day else None
    ref = clim_modern.get(md) if md else None

    pct_vs_median = None
    if latest_value is not None and ref and ref["median"] not in (None, 0):
        pct_vs_median = round((latest_value / ref["median"] - 1) * 100, 1)

    recent_days = sorted(rolling)[-120:]

    return {
        "name": place["name"],
        "state": place["state"],
        "requested_coordinate": [place["lat"], place["lon"]],
        "returned_grid_coordinates": [list(x) for x in sorted(returned_coords)],
        "latest_available_date": latest_day,
        "rolling_30d_mm": latest_value,
        "reference_1991_2020": ref,
        "category": category(latest_value, ref),
        "percent_vs_median": pct_vs_median,
        "recent_rolling_30d": [[d, rolling[d]] for d in recent_days],
        "climatology": {
            "1961-1990": clim_old,
            "1991-2020": clim_modern,
        },
    }


def main():
    places = municipality_lookup()
    print("Testorte:")
    for p in places:
        print(f'  {p["name"]}: {p["lat"]}, {p["lon"]}')

    text = fetch_csv(places)
    series, returned, fields = parse_csv(text, places)
    print("CSV-Spalten:", fields)

    output = {
        "status": "ok",
        "source": "GeoSphere Austria SPARTACUS v3 daily",
        "resource_id": "spartacus-v3-1d-1km",
        "parameter": "RR",
        "metric": "rolling_30d_precipitation",
        "unit": "mm",
        "reference_periods": ["1961-1990", "1991-2020"],
        "test_places": [],
    }

    for p in places:
        item = build_place_output(p, series[p["name"]], returned[p["name"]])
        output["test_places"].append(item)
        print(
            f'{p["name"]}: latest={item["latest_available_date"]}, '
            f'30d={item["rolling_30d_mm"]} mm, category={item["category"]}, '
            f'vs median={item["percent_vs_median"]}%'
        )

    save_json(OUT, output)
    print("Wrote", OUT)


if __name__ == "__main__":
    main()
