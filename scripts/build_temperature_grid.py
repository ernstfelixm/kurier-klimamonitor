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
TEMP_GRID = DATA / "temperature_grid"

API = "https://dataset.api.hub.geosphere.at/v1/timeseries/historical/spartacus-v3-1d-1km"
PARAMETER = "TX"
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
    tx_col = find_column(
        fields,
        exact=("TX",),
        contains=("tx [", "tx[", "maximum"),
    )

    if not time_col or not lat_col or not lon_col or not tx_col:
        raise RuntimeError(f"Unerwartete SPARTACUS-CSV-Spalten: {fields}")

    rows = []
    for row in reader:
        raw_time = row.get(time_col)
        lat = parse_float(row.get(lat_col))
        lon = parse_float(row.get(lon_col))
        tx = parse_float(row.get(tx_col))
        if not raw_time or lat is None or lon is None:
            continue
        rows.append((str(raw_time)[:10], lat, lon, tx))

    return rows


def grid_key(lat, lon):
    return f"{lat:.6f},{lon:.6f}"


def grid_id(lat, lon):
    # Deterministic ID: stable across repeated builds, no central counter needed.
    a = int(round((lat + 90) * 1_000_000))
    b = int(round((lon + 180) * 1_000_000))
    return f"g{a:09d}_{b:09d}"


def municipality_rows():
    obj = load_json(DATA / "precipitation" / "municipalities.json")

    cols = obj["columns"]
    ix = {name: i for i, name in enumerate(cols)}

    out = []

    for row in obj["municipalities"]:
        out.append(
            {
                "id": str(row[ix["id"]]),
                "name": row[ix["name"]],
                "state": row[ix["state"]],
                "state_slug": row[ix["state_slug"]],
                "grid_id": row[ix["grid_id"]],
                "grid_lat": float(row[ix["grid_lat"]]),
                "grid_lon": float(row[ix["grid_lon"]]),
            }
        )

    return out


def nearest_coord(lat, lon, coords):
    if not coords:
        raise RuntimeError("Keine SPARTACUS-Rasterkoordinate zurückgegeben.")
    return min(coords, key=lambda p: (p[0] - lat) ** 2 + (p[1] - lon) ** 2)


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


WINDOW_RADIUS_DAYS = 3
MIN_WINDOW_VALUES = 150

CALENDAR_START = date(2000, 1, 1)

CALENDAR_DAYS = [
    (
        CALENDAR_START
        + timedelta(days=i)
    ).strftime("%m-%d")
    for i in range(366)
]

CALENDAR_INDEX = {
    md: i
    for i, md in enumerate(CALENDAR_DAYS)
}


def calendar_window(month_day):
    center = CALENDAR_INDEX[month_day]

    return [
        CALENDAR_DAYS[
            (center + offset) % len(CALENDAR_DAYS)
        ]
        for offset in range(
            -WINDOW_RADIUS_DAYS,
            WINDOW_RADIUS_DAYS + 1,
        )
    ]


def split_reference_buckets(daily_values):
    old = {}
    modern = {}

    for day, value in daily_values.items():
        if value is None or not math.isfinite(value):
            continue

        try:
            d = date.fromisoformat(day)
        except ValueError:
            continue

        md = d.strftime("%m-%d")

        if 1961 <= d.year <= 1990:
            old.setdefault(md, []).append(value)

        elif 1991 <= d.year <= 2020:
            modern.setdefault(md, []).append(value)

    return old, modern


def stats_for_window(buckets, month_day):
    values = []

    for md in calendar_window(month_day):
        values.extend(
            buckets.get(md, [])
        )

    values = [
        v
        for v in values
        if v is not None and math.isfinite(v)
    ]

    n = len(values)

    if n < MIN_WINDOW_VALUES:
        return {
            "p20": None,
            "median": None,
            "p80": None,
            "n": n,
        }

    return {
        "p20": round(
            percentile(values, 0.20),
            1,
        ),
        "median": round(
            statistics.median(values),
            1,
        ),
        "p80": round(
            percentile(values, 0.80),
            1,
        ),
        "n": n,
    }


def climatology(daily_values):
    old_buckets, modern_buckets = (
        split_reference_buckets(
            daily_values
        )
    )

    out = {}

    for md in CALENDAR_DAYS:
        old = stats_for_window(
            old_buckets,
            md,
        )

        modern = stats_for_window(
            modern_buckets,
            md,
        )

        out[md] = [
            old["p20"],
            old["median"],
            old["p80"],
            modern["p20"],
            modern["median"],
            modern["p80"],
            old["n"],
            modern["n"],
        ]

    return out


def fetch_history_for_grids(grid_records):
    by_grid = {
        g["grid_id"]: {}
        for g in grid_records
    }

    for batch_no, batch in enumerate(
        chunks(grid_records, HISTORY_COORD_BATCH),
        start=1,
    ):
        print(
            f"Historie Batch {batch_no}: {len(batch)} Rasterpunkte "
            f"({HISTORY_START} bis {HISTORY_END})"
        )

        requested = [
            (
                g["grid_lat"],
                g["grid_lon"],
            )
            for g in batch
        ]

        rows = parse_rows(
            request_csv(
                requested,
                HISTORY_START,
                HISTORY_END,
            )
        )

        for day, lat, lon, tx in rows:
            glat, glon = nearest_coord(
                lat,
                lon,
                requested,
            )

            gid = grid_id(
                glat,
                glon,
            )

            if gid not in by_grid:
                continue

            by_grid[gid][day] = tx

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
    places = [
        p for p in all_places
        if p["state"] == state_name
    ]

    print()
    print("=" * 72)
    print(
        f"Baue Temperatur-Klimatologie für {state_name}: "
        f"{len(places)} Gemeinden"
    )
    print("=" * 72)

    unique = {}

    for p in places:
        unique[p["grid_id"]] = {
            "grid_id": p["grid_id"],
            "grid_lat": p["grid_lat"],
            "grid_lon": p["grid_lon"],
        }

    grids = [
        unique[k]
        for k in sorted(unique)
    ]

    print(
        f"{state_name}: "
        f"{len(grids)} eindeutige SPARTACUS-Rasterpunkte"
    )

    history = fetch_history_for_grids(grids)

    compact_grids = {}

    for i, grid in enumerate(
        grids,
        start=1,
    ):
        gid = grid["grid_id"]

        clim = climatology(
            history[gid]
        )

        rows = []

        for md in CALENDAR_DAYS:
            values = clim[md]
            rows.append(
                [md] + values
            )

        compact_grids[gid] = rows

        if i % 25 == 0 or i == len(grids):
            print(
                f"{state_name}: "
                f"Klimatologie {i}/{len(grids)}"
            )

    save_json(
        TEMP_GRID
        / "climatology"
        / f"{state_slug}.json",
        {
            "metric": "daily_max_temperature",
            "parameter": "TX",
            "unit": "°C",
            "window_radius_days": WINDOW_RADIUS_DAYS,
            "window_days": (
                WINDOW_RADIUS_DAYS * 2 + 1
            ),
            "minimum_window_values": MIN_WINDOW_VALUES,
            "reference_periods": [
                "1961-1990",
                "1991-2020",
            ],
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

    print(
        f"{state_name}: fertig."
    )


def write_build_status():
    municipalities_path = (
        DATA
        / "precipitation"
        / "municipalities.json"
    )

    built_states = []

    for state, slug in STATE_SLUGS.items():
        if (
            TEMP_GRID
            / "climatology"
            / f"{slug}.json"
        ).exists():
            built_states.append(state)

    mapped_count = 0
    unique_grids = set()

    if municipalities_path.exists():
        obj = load_json(
            municipalities_path
        )

        mapped_count = obj.get(
            "count",
            0,
        )

        cols = obj.get(
            "columns",
            [],
        )

        if "grid_id" in cols:
            ix = cols.index(
                "grid_id"
            )

            unique_grids = {
                row[ix]
                for row in obj.get(
                    "municipalities",
                    [],
                )
                if row[ix]
            }

    save_json(
        TEMP_GRID / "build_status.json",
        {
            "status": (
                "ok"
                if len(built_states)
                == len(STATE_SLUGS)
                else "partial"
            ),
            "source": (
                "GeoSphere Austria "
                "SPARTACUS v3 daily"
            ),
            "resource_id": (
                "spartacus-v3-1d-1km"
            ),
            "parameter": "TX",
            "metric": (
                "daily_max_temperature"
            ),
            "unit": "°C",
            "window_radius_days": (
                WINDOW_RADIUS_DAYS
            ),
            "window_days": (
                WINDOW_RADIUS_DAYS * 2 + 1
            ),
            "minimum_window_values": (
                MIN_WINDOW_VALUES
            ),
            "reference_periods": [
                "1961-1990",
                "1991-2020",
            ],
            "built_states": (
                built_states
            ),
            "mapped_municipality_count": (
                mapped_count
            ),
            "unique_grid_count": (
                len(unique_grids)
            ),
            "generated_at": (
                date.today().isoformat()
            ),
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
    print("Siehe docs/data/temperature_grid/build_status.json")


if __name__ == "__main__":
    main()
