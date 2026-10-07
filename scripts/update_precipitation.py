from __future__ import annotations

from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import csv
import io
import json
import math
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
PRECIP = DATA / "precipitation"

API = "https://dataset.api.hub.geosphere.at/v1/timeseries/historical/spartacus-v3-1d-1km"
PARAMETER = "RR"

# We need at least 29 source days before the first plotted rolling value.
# 150 source days leave enough headroom for a 90-day chart and revisions.
SOURCE_LOOKBACK_DAYS = 150
CURRENT_OUTPUT_DAYS = 120
COORD_BATCH = 120
TIMEOUT = 180
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
            print(
                f"GeoSphere: {len(coords)} Rasterpunkte, "
                f"{start} bis {end}, "
                f"datapoint-count={response.headers.get('datapoint-count')}"
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


def grid_id(lat, lon):
    a = int(round((lat + 90) * 1_000_000))
    b = int(round((lon + 180) * 1_000_000))
    return f"g{a:09d}_{b:09d}"


def nearest_coord(lat, lon, coords):
    return min(coords, key=lambda p: (p[0] - lat) ** 2 + (p[1] - lon) ** 2)


def rolling_30(day_values):
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


def load_mapping():
    path = PRECIP / "municipalities.json"
    if not path.exists():
        raise RuntimeError(
            "docs/data/precipitation/municipalities.json fehlt. "
            "Zuerst scripts/build_precipitation.py ausführen."
        )

    obj = load_json(path)
    cols = obj["columns"]
    ix = {name: i for i, name in enumerate(cols)}

    rows = []
    for row in obj["municipalities"]:
        rows.append(
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
    return rows


def now_vienna():
    return datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Vienna"))


def main():
    mapping = load_mapping()

    unique = {}
    for row in mapping:
        unique[row["grid_id"]] = (row["grid_lat"], row["grid_lon"])

    grid_records = [
        {"grid_id": gid, "lat": coord[0], "lon": coord[1]}
        for gid, coord in sorted(unique.items())
    ]

    local_now = now_vienna()
    end = local_now.date() - timedelta(days=1)
    start = end - timedelta(days=SOURCE_LOOKBACK_DAYS - 1)

    daily = {g["grid_id"]: {} for g in grid_records}

    for batch_no, batch in enumerate(chunks(grid_records, COORD_BATCH), start=1):
        print(f"Aktueller Niederschlag Batch {batch_no}: {len(batch)} Rasterpunkte")
        coords = [(g["lat"], g["lon"]) for g in batch]
        rows = parse_rows(request_csv(coords, start, end))

        for day, lat, lon, rr in rows:
            glat, glon = nearest_coord(lat, lon, coords)
            gid = grid_id(glat, glon)
            if gid in daily:
                daily[gid][day] = rr

    rolling = {gid: rolling_30(values) for gid, values in daily.items()}

    latest = None
    for values in rolling.values():
        if values:
            candidate = max(values)
            if latest is None or candidate > latest:
                latest = candidate

    if latest is None:
        raise RuntimeError("Keine aktuellen rollierenden Niederschlagswerte berechnet.")

    by_state_grids = {}
    for row in mapping:
        by_state_grids.setdefault(row["state_slug"], set()).add(row["grid_id"])

    for state_slug, gids in sorted(by_state_grids.items()):
        grids = {}
        for gid in sorted(gids):
            values = rolling.get(gid, {})
            days = sorted(values)[-CURRENT_OUTPUT_DAYS:]
            grids[gid] = [[d, values[d]] for d in days]

        save_json(
            PRECIP / "current" / f"{state_slug}.json",
            {
                "metric": "rolling_30d_precipitation",
                "parameter": "RR",
                "unit": "mm",
                "rolling_window_days": 30,
                "columns": ["date", "rr30"],
                "latest_available_date": latest,
                "grid_count": len(grids),
                "grids": grids,
            },
        )

    save_json(
        PRECIP / "status.json",
        {
            "status": "ok",
            "source": "GeoSphere Austria SPARTACUS v3 daily",
            "resource_id": "spartacus-v3-1d-1km",
            "parameter": "RR",
            "metric": "rolling_30d_precipitation",
            "unit": "mm",
            "rolling_window_days": 30,
            "latest_available_date": latest,
            "grid_count": len(grid_records),
            "updated_at": local_now.isoformat(timespec="seconds"),
            "note": (
                "SPARTACUS revises recent days; this updater re-fetches a "
                f"{SOURCE_LOOKBACK_DAYS}-day source window on every run."
            ),
        },
    )

    print(
        f"Fertig: {len(grid_records)} Rasterpunkte, "
        f"neuester 30-Tage-Wert {latest}"
    )


if __name__ == "__main__":
    main()
