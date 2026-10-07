from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta
import argparse
import csv
import io
import json
import math
import statistics
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
PRECIP = DATA / "precipitation"

API = "https://dataset.api.hub.geosphere.at/v1/timeseries/historical/spartacus-v3-1d-1km"
PARAMETER = "RR"
HISTORY_START = date(1961, 1, 1)
HISTORY_END = date(2020, 12, 31)

# ~21,915 daily values per coordinate for 1961-2020.
# 35 coordinates stay comfortably below the API's 1,000,000-value limit.
HISTORY_COORD_BATCH = 35

# Mapping requests only cover a short recent period, so larger batches are fine.
MAPPING_PLACE_BATCH = 180
TIMEOUT = 240
RETRIES = 4

STATE_SLUGS = {
    "Burgenland": "burgenland",
    "Kärnten": "kaernten",
    "Niederösterreich": "niederoesterreich",
    "Oberösterreich": "oberoesterreich",
    "Salzburg": "salzburg",
    "Steiermark": "steiermark",
    "Tirol": "tirol",
    "Vorarlberg": "vorarlberg",
    "Wien": "wien",
}
STATE_ORDER = list(STATE_SLUGS)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def parse_float(value):
    if value in (None, "", "nan", "NaN", "null", "None"):
        return None
    try:
        x = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def find_column(fieldnames, exact=(), contains=()):
    fields = list(fieldnames or [])
    low = {str(name).lower(): name for name in fields if name is not None}
    for key in exact:
        if key.lower() in low:
            return low[key.lower()]
    for name in fields:
        lname = str(name).lower()
        if any(token.lower() in lname for token in contains):
            return name
    return None


def request_csv(coords, start: date, end: date):
    params = [
        ("parameters", PARAMETER),
        ("start", start.isoformat()),
        ("end", end.isoformat()),
        ("output_format", "csv"),
    ]
    params += [("lat_lon", f"{lat:.6f},{lon:.6f}") for lat, lon in coords]

    last_error = None
    for attempt in range(RETRIES):
        try:
            response = requests.get(API, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            count = response.headers.get("datapoint-count")
            print(
                f"GeoSphere: {len(coords)} Koordinaten, "
                f"{start} bis {end}, datapoint-count={count}"
            )
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt + 1 < RETRIES:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"GeoSphere request failed: {last_error}")


def parse_rows(text):
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []

    time_col = find_column(
        fields, exact=("time", "date", "timestamp"), contains=("time", "date")
    )
    lat_col = find_column(
        fields, exact=("lat", "latitude"), contains=("latitude", "lat")
    )
    lon_col = find_column(
        fields, exact=("lon", "longitude"), contains=("longitude", "lon")
    )
    rr_col = find_column(
        fields, exact=("RR",), contains=("rr [", "rr[", "precip")
    )

    if not time_col or not lat_col or not lon_col or not rr_col:
        raise RuntimeError(f"Unerwartete SPARTACUS-CSV-Spalten: {fields}")

    rows = []
    for row in reader:
        raw_time = row.get(time_col)
        lat = parse_float(row.get(lat_col))
        lon = parse_float(row.get(lon_col))
        rr = parse_float(row.get(rr_col))
        if not raw_time or lat is None or lon is None:
            continue
        rows.append((str(raw_time)[:10], lat, lon, rr))

    return rows


def grid_key(lat, lon):
    return f"{lat:.6f},{lon:.6f}"


def grid_id(lat, lon):
    # Deterministic ID: stable across repeated builds, no central counter needed.
    a = int(round((lat + 90) * 1_000_000))
    b = int(round((lon + 180) * 1_000_000))
    return f"g{a:09d}_{b:09d}"


def municipality_rows():
    obj = load_json(DATA / "municipalities.json")
    cols = obj["columns"]
    ix = {name: i for i, name in enumerate(cols)}

    out = []
    for row in obj["municipalities"]:
        state = row[ix["state"]]
        if state not in STATE_SLUGS:
            raise RuntimeError(f"Unbekanntes Bundesland: {state}")
        out.append(
            {
                "id": str(row[ix["id"]]),
                "name": row[ix["name"]],
                "state": state,
                "state_slug": STATE_SLUGS[state],
                "lat": float(row[ix["lat"]]),
                "lon": float(row[ix["lon"]]),
            }
        )
    return out


def nearest_coord(lat, lon, coords):
    if not coords:
        raise RuntimeError("Keine SPARTACUS-Rasterkoordinate zurückgegeben.")
    return min(coords, key=lambda p: (p[0] - lat) ** 2 + (p[1] - lon) ** 2)


def map_municipalities(places):
    """
    Ask SPARTACUS for a short recent window and record which 1-km grid cell is
    returned for every municipality coordinate.
    """
    # Avoid today because the daily grid may not yet be published.
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=13)

    mapped = []

    for batch_no, batch in enumerate(chunks(places, MAPPING_PLACE_BATCH), start=1):
        print(
            f"Rasterzuordnung Batch {batch_no}: "
            f"{len(batch)} Gemeinden ({batch[0]['state']})"
        )
        requested = [(p["lat"], p["lon"]) for p in batch]
        rows = parse_rows(request_csv(requested, start, end))

        returned = sorted(
            {
                (round(lat, 6), round(lon, 6))
                for _day, lat, lon, _rr in rows
            }
        )
        if not returned:
            raise RuntimeError(
                f"Keine Rasterpunkte für Mapping-Batch {batch_no} zurückgegeben."
            )

        for place in batch:
            glat, glon = nearest_coord(place["lat"], place["lon"], returned)
            mapped.append(
                {
                    **place,
                    "grid_id": grid_id(glat, glon),
                    "grid_lat": glat,
                    "grid_lon": glon,
                }
            )

    return mapped


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
    """
    Exact 30-calendar-day sum. A value is emitted only if all 30 daily RR
    values exist, so missing source days never silently become zero.
    """
    if not day_values:
        return {}

    dates = sorted(date.fromisoformat(d) for d in day_values)
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


def climatology(rolling):
    buckets_old = {}
    buckets_modern = {}

    for day, value in rolling.items():
        year = int(day[:4])
        md = day[5:]
        if 1961 <= year <= 1990:
            buckets_old.setdefault(md, []).append(value)
        elif 1991 <= year <= 2020:
            buckets_modern.setdefault(md, []).append(value)

    out = {}
    all_days = sorted(set(buckets_old) | set(buckets_modern))

    for md in all_days:
        old = buckets_old.get(md, [])
        modern = buckets_modern.get(md, [])

        def stats(values):
            if len(values) < 20:
                return [None, None, None]
            return [
                round(percentile(values, 0.20), 1),
                round(statistics.median(values), 1),
                round(percentile(values, 0.80), 1),
            ]

        o20, omed, o80 = stats(old)
        m20, mmed, m80 = stats(modern)
        out[md] = [
            o20,
            omed,
            o80,
            m20,
            mmed,
            m80,
            len(old),
            len(modern),
        ]

    return out


def fetch_history_for_grids(grid_records):
    coords = [(g["lat"], g["lon"]) for g in grid_records]
    by_grid = {g["grid_id"]: {} for g in grid_records}

    for batch_no, batch in enumerate(chunks(grid_records, HISTORY_COORD_BATCH), start=1):
        print(
            f"Historie Batch {batch_no}: {len(batch)} Rasterpunkte "
            f"({HISTORY_START} bis {HISTORY_END})"
        )
        requested = [(g["lat"], g["lon"]) for g in batch]
        rows = parse_rows(request_csv(requested, HISTORY_START, HISTORY_END))

        for day, lat, lon, rr in rows:
            glat, glon = nearest_coord(lat, lon, requested)
            gid = grid_id(glat, glon)
            if gid not in by_grid:
                continue
            by_grid[gid][day] = rr

    return by_grid


def merge_municipality_mapping(mapped_state):
    path = PRECIP / "municipalities.json"
    existing = []

    if path.exists():
        obj = load_json(path)
        cols = obj.get("columns", [])
        if cols:
            ix = {name: i for i, name in enumerate(cols)}
            for row in obj.get("municipalities", []):
                existing.append(
                    {
                        "id": str(row[ix["id"]]),
                        "name": row[ix["name"]],
                        "state": row[ix["state"]],
                        "state_slug": row[ix["state_slug"]],
                        "grid_id": row[ix["grid_id"]],
                        "grid_lat": row[ix["grid_lat"]],
                        "grid_lon": row[ix["grid_lon"]],
                    }
                )

    state_slug = mapped_state[0]["state_slug"]
    existing = [x for x in existing if x["state_slug"] != state_slug]

    existing.extend(
        {
            "id": p["id"],
            "name": p["name"],
            "state": p["state"],
            "state_slug": p["state_slug"],
            "grid_id": p["grid_id"],
            "grid_lat": round(p["grid_lat"], 6),
            "grid_lon": round(p["grid_lon"], 6),
        }
        for p in mapped_state
    )

    existing.sort(key=lambda x: (x["state"], x["name"], x["id"]))

    cols = [
        "id",
        "name",
        "state",
        "state_slug",
        "grid_id",
        "grid_lat",
        "grid_lon",
    ]
    save_json(
        path,
        {
            "version": date.today().isoformat(),
            "count": len(existing),
            "columns": cols,
            "municipalities": [[x[c] for c in cols] for x in existing],
        },
    )


def build_state(state_name, all_places):
    state_slug = STATE_SLUGS[state_name]
    places = [p for p in all_places if p["state"] == state_name]

    print()
    print("=" * 72)
    print(f"Baue Niederschlags-Klimatologie für {state_name}: {len(places)} Gemeinden")
    print("=" * 72)

    mapped = map_municipalities(places)
    merge_municipality_mapping(mapped)

    unique = {}
    for p in mapped:
        unique[p["grid_id"]] = {
            "grid_id": p["grid_id"],
            "lat": p["grid_lat"],
            "lon": p["grid_lon"],
        }

    grids = [unique[k] for k in sorted(unique)]
    print(f"{state_name}: {len(grids)} eindeutige SPARTACUS-Rasterpunkte")

    history = fetch_history_for_grids(grids)

    compact_grids = {}
    for i, grid in enumerate(grids, start=1):
        gid = grid["grid_id"]
        rolling = rolling_30(history[gid])
        clim = climatology(rolling)

        rows = []
        for md in sorted(clim):
            values = clim[md]
            rows.append([md] + values)

        compact_grids[gid] = rows

        if i % 25 == 0 or i == len(grids):
            print(f"{state_name}: Klimatologie {i}/{len(grids)}")

    save_json(
        PRECIP / "climatology" / f"{state_slug}.json",
        {
            "metric": "rolling_30d_precipitation",
            "parameter": "RR",
            "unit": "mm",
            "rolling_window_days": 30,
            "reference_periods": ["1961-1990", "1991-2020"],
            "columns": [
                "day",
                "p20_1961_1990",
                "median_1961_1990",
                "p80_1961_1990",
                "p20_1991_2020",
                "median_1991_2020",
                "p80_1991_2020",
                "n_1961_1990",
                "n_1991_2020",
            ],
            "grid_count": len(compact_grids),
            "grids": compact_grids,
        },
    )

    print(f"{state_name}: fertig.")


def write_build_status():
    municipalities_path = PRECIP / "municipalities.json"
    built_states = []

    for state, slug in STATE_SLUGS.items():
        if (PRECIP / "climatology" / f"{slug}.json").exists():
            built_states.append(state)

    mapped_count = 0
    unique_grids = set()
    if municipalities_path.exists():
        obj = load_json(municipalities_path)
        mapped_count = obj.get("count", 0)
        cols = obj.get("columns", [])
        if "grid_id" in cols:
            ix = cols.index("grid_id")
            unique_grids = {
                row[ix] for row in obj.get("municipalities", []) if row[ix]
            }

    save_json(
        PRECIP / "build_status.json",
        {
            "status": "ok" if len(built_states) == len(STATE_SLUGS) else "partial",
            "source": "GeoSphere Austria SPARTACUS v3 daily",
            "resource_id": "spartacus-v3-1d-1km",
            "parameter": "RR",
            "metric": "rolling_30d_precipitation",
            "unit": "mm",
            "rolling_window_days": 30,
            "reference_periods": ["1961-1990", "1991-2020"],
            "built_states": built_states,
            "mapped_municipality_count": mapped_count,
            "unique_grid_count": len(unique_grids),
            "generated_at": date.today().isoformat(),
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state",
        default="all",
        help=(
            "Bundesland oder 'all'. Erlaubt: "
            + ", ".join(STATE_ORDER)
        ),
    )
    args = parser.parse_args()

    all_places = municipality_rows()

    if args.state == "all":
        states = STATE_ORDER
    else:
        if args.state not in STATE_SLUGS:
            raise SystemExit(
                f"Unbekanntes Bundesland: {args.state}. "
                f"Erlaubt: {', '.join(STATE_ORDER)} oder all"
            )
        states = [args.state]

    for state in states:
        build_state(state, all_places)
        write_build_status()

    print()
    print("Produktions-Build abgeschlossen.")
    print("Siehe docs/data/precipitation/build_status.json")


if __name__ == "__main__":
    main()
