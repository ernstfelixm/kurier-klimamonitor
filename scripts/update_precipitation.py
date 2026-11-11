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

SPARTACUS_API = (
    "https://dataset.api.hub.geosphere.at/v1/timeseries/historical/"
    "spartacus-v3-1d-1km"
)
INCA_API = (
    "https://dataset.api.hub.geosphere.at/v1/timeseries/historical/"
    "inca-v2-1h-1km"
)

SPARTACUS_PARAMETER = "RR"
INCA_PARAMETER = "tp"

VIENNA = ZoneInfo("Europe/Vienna")

# We need at least 29 source days before the first plotted rolling value.
# 150 source days leave enough headroom for a 90-day chart and revisions.
SOURCE_LOOKBACK_DAYS = 150
CURRENT_OUTPUT_DAYS = 120

# INCA is only a short provisional bridge until SPARTACUS catches up.
INCA_MAX_FALLBACK_DAYS = 3

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
    low = {
        str(name).lower(): name
        for name in fields
        if name is not None
    }

    for key in exact:
        if key.lower() in low:
            return low[key.lower()]

    for name in fields:
        lname = str(name).lower()
        if any(token.lower() in lname for token in contains):
            return name

    return None


def request_text(url, params):
    last_error = None

    for attempt in range(RETRIES):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            return response

        except Exception as exc:
            last_error = exc

            if attempt + 1 < RETRIES:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"GeoSphere request failed: {last_error}")


def request_spartacus_csv(coords, start: date, end: date):
    params = [
        ("parameters", SPARTACUS_PARAMETER),
        ("start", start.isoformat()),
        ("end", end.isoformat()),
        ("output_format", "csv"),
    ]

    params += [
        ("lat_lon", f"{lat:.6f},{lon:.6f}")
        for lat, lon in coords
    ]

    response = request_text(SPARTACUS_API, params)

    print(
        f"SPARTACUS: {len(coords)} Rasterpunkte, "
        f"{start} bis {end}, "
        f"datapoint-count={response.headers.get('datapoint-count')}"
    )

    return response.text


def request_inca_csv(
    coords,
    start_day: date,
    end_day: date,
):
    # Padding is deliberate.
    #
    # INCA values are hourly accumulations with timestamps referring to
    # the END of the accumulation hour. We therefore request extra data
    # around the target interval and assign/filter by local calendar day
    # after converting to Europe/Vienna.
    padded_start = start_day - timedelta(days=1)
    padded_end = end_day + timedelta(days=2)

    start_text = padded_start.isoformat() + "T00:00"
    end_text = padded_end.isoformat() + "T00:00"

    params = [
        ("parameters", INCA_PARAMETER),
        ("start", start_text),
        ("end", end_text),
        ("output_format", "csv"),
    ]

    params += [
        ("lat_lon", f"{lat:.6f},{lon:.6f}")
        for lat, lon in coords
    ]

    response = request_text(INCA_API, params)

    print(
        f"INCA: {len(coords)} Rasterpunkte, "
        f"Ziel {start_day} bis {end_day}, "
        f"Request {start_text} bis {end_text}, "
        f"datapoint-count={response.headers.get('datapoint-count')}"
    )

    return response.text


def parse_spartacus_rows(text):
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []

    time_col = find_column(
        fields,
        exact=("time", "date", "timestamp"),
        contains=("time", "date"),
    )
    lat_col = find_column(
        fields,
        exact=("lat", "latitude"),
        contains=("latitude", "lat"),
    )
    lon_col = find_column(
        fields,
        exact=("lon", "longitude"),
        contains=("longitude", "lon"),
    )
    rr_col = find_column(
        fields,
        exact=("RR",),
        contains=("rr [", "rr[", "precip"),
    )

    if not all((time_col, lat_col, lon_col, rr_col)):
        raise RuntimeError(
            f"Unerwartete SPARTACUS-CSV-Spalten: {fields}"
        )

    rows = []

    for row in reader:
        raw_time = row.get(time_col)
        lat = parse_float(row.get(lat_col))
        lon = parse_float(row.get(lon_col))
        rr = parse_float(row.get(rr_col))

        if not raw_time or lat is None or lon is None:
            continue

        rows.append(
            (
                str(raw_time)[:10],
                lat,
                lon,
                rr,
            )
        )

    return rows


def parse_iso_datetime(value: str):
    s = str(value).strip()

    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    dt = datetime.fromisoformat(s)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def parse_inca_rows(text):
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []

    time_col = find_column(
        fields,
        exact=("time", "date", "timestamp"),
        contains=("time", "date"),
    )
    lat_col = find_column(
        fields,
        exact=("lat", "latitude"),
        contains=("latitude", "lat"),
    )
    lon_col = find_column(
        fields,
        exact=("lon", "longitude"),
        contains=("longitude", "lon"),
    )
    tp_col = find_column(
        fields,
        exact=("tp",),
        contains=("tp [", "tp[", "precip"),
    )

    if not all((time_col, lat_col, lon_col, tp_col)):
        raise RuntimeError(
            f"Unerwartete INCA-CSV-Spalten: {fields}"
        )

    rows = []

    for row in reader:
        raw_time = row.get(time_col)
        lat = parse_float(row.get(lat_col))
        lon = parse_float(row.get(lon_col))
        tp = parse_float(row.get(tp_col))

        if (
            not raw_time
            or lat is None
            or lon is None
            or tp is None
        ):
            continue

        rows.append(
            (
                parse_iso_datetime(raw_time),
                lat,
                lon,
                tp,
            )
        )

    return rows


def grid_id(lat, lon):
    a = int(round((lat + 90) * 1_000_000))
    b = int(round((lon + 180) * 1_000_000))

    return f"g{a:09d}_{b:09d}"


def coord_key(lat, lon):
    return (
        round(float(lat), 6),
        round(float(lon), 6),
    )


def nearest_coord(lat, lon, coords):
    return min(
        coords,
        key=lambda p: (
            (p[0] - lat) ** 2
            + (p[1] - lon) ** 2
        ),
    )


def expected_local_hours(day: date):
    local_start = datetime(
        day.year,
        day.month,
        day.day,
        tzinfo=VIENNA,
    )

    local_end = local_start + timedelta(days=1)

    utc_start = local_start.astimezone(timezone.utc)
    utc_end = local_end.astimezone(timezone.utc)

    return int(
        (utc_end - utc_start).total_seconds() // 3600
    )


def aggregate_inca(rows, requested_coords):
    """
    Aggregate INCA hourly precipitation to local calendar days.

    INCA 'tp' is an hourly accumulation and the timestamp represents the
    END of the accumulation interval. This convention was tested against
    SPARTACUS in the overlap test.

    Subtracting one microsecond assigns a timestamp such as local 00:00
    to the preceding accumulation hour/day.
    """

    by_coord = {
        coord_key(*coord): {}
        for coord in requested_coords
    }

    for ts_utc, lat, lon, tp in rows:
        requested = nearest_coord(
            lat,
            lon,
            requested_coords,
        )

        key = coord_key(*requested)

        # Tested "end" timestamp convention.
        effective_ts = ts_utc - timedelta(microseconds=1)
        local_ts = effective_ts.astimezone(VIENNA)
        local_day = local_ts.date()

        by_coord[key].setdefault(
            local_day,
            [],
        ).append(tp)

    out = {
        key: {}
        for key in by_coord
    }

    counts = {
        key: {}
        for key in by_coord
    }

    for key, days in by_coord.items():
        for day, values in days.items():
            observed = len(values)
            expected = expected_local_hours(day)

            counts[key][day] = observed

            if observed == expected:
                out[key][day] = sum(values)
            else:
                # Never interpret missing hours as zero.
                out[key][day] = None

    return out, counts


def rolling_30(day_values):
    if not day_values:
        return {}

    dates = sorted(
        date.fromisoformat(d)
        for d in day_values
    )

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

        # A rolling value only exists when every one of the
        # 30 source days is complete.
        if (
            len(window) == 30
            and all(v is not None for v in window)
        ):
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
    ix = {
        name: i
        for i, name in enumerate(cols)
    }

    rows = []

    for row in obj["municipalities"]:
        rows.append(
            {
                "id": str(row[ix["id"]]),
                "name": row[ix["name"]],
                "state": row[ix["state"]],
                "state_slug": row[ix["state_slug"]],
                "grid_id": row[ix["grid_id"]],
                "grid_lat": float(
                    row[ix["grid_lat"]]
                ),
                "grid_lon": float(
                    row[ix["grid_lon"]]
                ),
            }
        )

    return rows


def now_vienna():
    return datetime.now(
        timezone.utc
    ).astimezone(VIENNA)


def find_latest_valid_day(values, end_day):
    valid = []

    for raw_day, value in values.items():
        if value is None:
            continue

        d = date.fromisoformat(raw_day)

        if d <= end_day:
            valid.append(d)

    return max(valid) if valid else None


def build_inca_fallback_plan(
    grid_records,
    daily,
    target_end,
):
    """
    Determine which grid points may use INCA.

    Important:
    - INCA only fills dates AFTER the last valid SPARTACUS value.
    - Maximum gap is 3 days.
    - Longer gaps are intentionally not bridged.
    """

    plan = {}

    for grid in grid_records:
        gid = grid["grid_id"]

        last_spartacus = find_latest_valid_day(
            daily[gid],
            target_end,
        )

        if last_spartacus is None:
            continue

        gap_days = (
            target_end - last_spartacus
        ).days

        if (
            gap_days < 1
            or gap_days > INCA_MAX_FALLBACK_DAYS
        ):
            continue

        target_days = [
            last_spartacus + timedelta(days=i)
            for i in range(1, gap_days + 1)
        ]

        plan[gid] = {
            "grid": grid,
            "last_spartacus": last_spartacus,
            "target_days": target_days,
        }

    return plan


def apply_inca_fallback(
    grid_records,
    daily,
    target_end,
):
    plan = build_inca_fallback_plan(
        grid_records,
        daily,
        target_end,
    )

    stats = {
        "eligible_grid_count": len(plan),
        "updated_grid_count": 0,
        "inserted_grid_days": 0,
        "incomplete_grid_days": 0,
        "earliest_fallback_date": None,
        "latest_fallback_date": None,
    }

    if not plan:
        print(
            "INCA-Fallback: nicht erforderlich oder "
            "SPARTACUS-Lücke größer als "
            f"{INCA_MAX_FALLBACK_DAYS} Tage."
        )
        return stats

    all_target_days = [
        d
        for item in plan.values()
        for d in item["target_days"]
    ]

    fallback_start = min(all_target_days)
    fallback_end = max(all_target_days)

    print(
        "INCA-Fallback geplant: "
        f"{fallback_start} bis {fallback_end}, "
        f"{len(plan)} Rasterpunkte"
    )

    eligible_records = [
        item["grid"]
        for item in plan.values()
    ]

    updated_gids = set()

    for batch_no, batch in enumerate(
        chunks(
            eligible_records,
            COORD_BATCH,
        ),
        start=1,
    ):
        print(
            f"INCA Batch {batch_no}: "
            f"{len(batch)} Rasterpunkte"
        )

        coords = [
            (g["lat"], g["lon"])
            for g in batch
        ]

        coord_to_gid = {
            coord_key(
                g["lat"],
                g["lon"],
            ): g["grid_id"]
            for g in batch
        }

        text = request_inca_csv(
            coords,
            fallback_start,
            fallback_end,
        )

        rows = parse_inca_rows(text)

        aggregated, counts = aggregate_inca(
            rows,
            coords,
        )

        for coord in coords:
            key = coord_key(*coord)
            gid = coord_to_gid[key]

            target_days = plan[gid]["target_days"]

            for day in target_days:
                # SPARTACUS always has priority.
                existing = daily[gid].get(
                    day.isoformat()
                )

                if existing is not None:
                    continue

                value = aggregated.get(
                    key,
                    {},
                ).get(day)

                if value is None:
                    stats[
                        "incomplete_grid_days"
                    ] += 1

                    observed = counts.get(
                        key,
                        {},
                    ).get(day)

                    print(
                        "WARNUNG INCA unvollständig: "
                        f"{gid} {day}, "
                        f"Stunden={observed}, "
                        f"erwartet={expected_local_hours(day)}"
                    )

                    continue

                daily[gid][
                    day.isoformat()
                ] = value

                updated_gids.add(gid)

                stats[
                    "inserted_grid_days"
                ] += 1

                if (
                    stats["earliest_fallback_date"] is None
                    or day.isoformat()
                    < stats["earliest_fallback_date"]
                ):
                    stats[
                        "earliest_fallback_date"
                    ] = day.isoformat()

                if (
                    stats["latest_fallback_date"] is None
                    or day.isoformat()
                    > stats["latest_fallback_date"]
                ):
                    stats[
                        "latest_fallback_date"
                    ] = day.isoformat()

    stats["updated_grid_count"] = len(
        updated_gids
    )

    print(
        "INCA-Fallback fertig: "
        f"{stats['updated_grid_count']} Rasterpunkte, "
        f"{stats['inserted_grid_days']} vollständige "
        "Rasterpunkt-Tage ergänzt, "
        f"{stats['incomplete_grid_days']} "
        "unvollständige Rasterpunkt-Tage verworfen."
    )

    return stats


def main():
    mapping = load_mapping()

    unique = {}

    for row in mapping:
        unique[row["grid_id"]] = (
            row["grid_lat"],
            row["grid_lon"],
        )

    grid_records = [
        {
            "grid_id": gid,
            "lat": coord[0],
            "lon": coord[1],
        }
        for gid, coord
        in sorted(unique.items())
    ]

    local_now = now_vienna()

    # Never use the current, potentially incomplete local day.
    target_end = (
        local_now.date()
        - timedelta(days=1)
    )

    source_start = (
        target_end
        - timedelta(
            days=SOURCE_LOOKBACK_DAYS - 1
        )
    )

    daily = {
        g["grid_id"]: {}
        for g in grid_records
    }

    # ---------------------------------------------------------
    # 1. SPARTACUS – primary source
    # ---------------------------------------------------------

    for batch_no, batch in enumerate(
        chunks(
            grid_records,
            COORD_BATCH,
        ),
        start=1,
    ):
        print(
            "Aktueller Niederschlag "
            f"SPARTACUS Batch {batch_no}: "
            f"{len(batch)} Rasterpunkte"
        )

        coords = [
            (g["lat"], g["lon"])
            for g in batch
        ]

        rows = parse_spartacus_rows(
            request_spartacus_csv(
                coords,
                source_start,
                target_end,
            )
        )

        for day, lat, lon, rr in rows:
            glat, glon = nearest_coord(
                lat,
                lon,
                coords,
            )

            gid = grid_id(
                glat,
                glon,
            )

            if gid in daily:
                daily[gid][day] = rr

    spartacus_latest_dates = []

    for values in daily.values():
        d = find_latest_valid_day(
            values,
            target_end,
        )

        if d is not None:
            spartacus_latest_dates.append(d)

    latest_spartacus = (
        max(spartacus_latest_dates)
        if spartacus_latest_dates
        else None
    )

    if latest_spartacus is None:
        raise RuntimeError(
            "Keine gültigen SPARTACUS-"
            "Niederschlagswerte gefunden."
        )

    print(
        "SPARTACUS neuester verfügbarer Tag: "
        f"{latest_spartacus}"
    )

    # ---------------------------------------------------------
    # 2. INCA – provisional fallback for at most 3 latest days
    # ---------------------------------------------------------

    inca_stats = apply_inca_fallback(
        grid_records,
        daily,
        target_end,
    )

    # ---------------------------------------------------------
    # 3. Strict rolling 30-day totals
    # ---------------------------------------------------------

    rolling = {
        gid: rolling_30(values)
        for gid, values in daily.items()
    }

    latest = None

    for values in rolling.values():
        if not values:
            continue

        candidate = max(values)

        if (
            latest is None
            or candidate > latest
        ):
            latest = candidate

    if latest is None:
        raise RuntimeError(
            "Keine aktuellen rollierenden "
            "Niederschlagswerte berechnet."
        )

    # ---------------------------------------------------------
    # 4. State files
    # ---------------------------------------------------------

    by_state_grids = {}

    for row in mapping:
        by_state_grids.setdefault(
            row["state_slug"],
            set(),
        ).add(row["grid_id"])

    for state_slug, gids in sorted(
        by_state_grids.items()
    ):
        grids = {}

        for gid in sorted(gids):
            values = rolling.get(
                gid,
                {},
            )

            days = sorted(values)[
                -CURRENT_OUTPUT_DAYS:
            ]

            grids[gid] = [
                [d, values[d]]
                for d in days
            ]

        save_json(
            PRECIP
            / "current"
            / f"{state_slug}.json",
            {
                "metric": (
                    "rolling_30d_precipitation"
                ),
                "parameter": "RR",
                "unit": "mm",
                "rolling_window_days": 30,
                "columns": [
                    "date",
                    "rr30",
                ],
                "latest_available_date": latest,
                "grid_count": len(grids),
                "grids": grids,
            },
        )

    # ---------------------------------------------------------
    # 5. Status / provenance
    # ---------------------------------------------------------

    source_label = (
        "GeoSphere Austria SPARTACUS v3 daily"
    )

    if inca_stats["inserted_grid_days"] > 0:
        source_label += (
            " + INCA v2 hourly fallback"
        )

    save_json(
        PRECIP / "status.json",
        {
            "status": "ok",
            "source": source_label,
            "primary_resource_id": (
                "spartacus-v3-1d-1km"
            ),
            "primary_parameter": (
                SPARTACUS_PARAMETER
            ),
            "fallback_resource_id": (
                "inca-v2-1h-1km"
            ),
            "fallback_parameter": (
                INCA_PARAMETER
            ),
            "fallback_max_days": (
                INCA_MAX_FALLBACK_DAYS
            ),
            "fallback_timestamp_assignment": (
                "end"
            ),
            "fallback_timezone": (
                "Europe/Vienna"
            ),
            "fallback_eligible_grid_count": (
                inca_stats[
                    "eligible_grid_count"
                ]
            ),
            "fallback_updated_grid_count": (
                inca_stats[
                    "updated_grid_count"
                ]
            ),
            "fallback_inserted_grid_days": (
                inca_stats[
                    "inserted_grid_days"
                ]
            ),
            "fallback_incomplete_grid_days": (
                inca_stats[
                    "incomplete_grid_days"
                ]
            ),
            "fallback_earliest_date": (
                inca_stats[
                    "earliest_fallback_date"
                ]
            ),
            "fallback_latest_date": (
                inca_stats[
                    "latest_fallback_date"
                ]
            ),
            "spartacus_latest_available_date": (
                latest_spartacus.isoformat()
            ),
            "metric": (
                "rolling_30d_precipitation"
            ),
            "unit": "mm",
            "rolling_window_days": 30,
            "latest_available_date": latest,
            "grid_count": len(grid_records),
            "updated_at": local_now.isoformat(
                timespec="seconds"
            ),
            "note": (
                "SPARTACUS is the primary source and "
                "revises recent days. The updater "
                f"re-fetches a {SOURCE_LOOKBACK_DAYS}-day "
                "SPARTACUS source window on every run. "
                "Only complete local calendar days after "
                "the latest valid SPARTACUS day may be "
                "provisionally filled from INCA, with a "
                f"maximum of {INCA_MAX_FALLBACK_DAYS} "
                "days. Existing SPARTACUS values are "
                "never overwritten by INCA. Missing INCA "
                "hours are never interpreted as zero."
            ),
        },
    )

    print(
        f"Fertig: {len(grid_records)} Rasterpunkte, "
        f"neuester 30-Tage-Wert {latest}"
    )


if __name__ == "__main__":
    main()
