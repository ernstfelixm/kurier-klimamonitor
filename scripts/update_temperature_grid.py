
from pathlib import Path
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo
import csv
import io
import json
import math
import time

import requests


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
TEMP_GRID = DATA / "temperature_grid"

API = (
    "https://dataset.api.hub.geosphere.at/v1/"
    "timeseries/historical/spartacus-v3-1d-1km"
)
PARAMETER = "TX"

# Historie für die 90-Tage-Ansicht
# einschließlich Sicherheitsreserve.
HISTORY_DAYS = 120

# Tägliche Aktualisierung bestehender Rasterpunkte.
LOOKBACK_DAYS = 14

# Mindestabdeckung der älteren Historie.
MIN_HISTORY_COVERAGE = 0.98

BATCH_SIZE = 100
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


def load_json(path):
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def save_json(path, obj):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    temporary.replace(path)


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def parse_float(value):
    if value in (
        None,
        "",
        "nan",
        "NaN",
        "null",
        "None",
    ):
        return None

    try:
        number = float(
            str(value).replace(",", ".")
        )
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def find_column(
    fieldnames,
    exact=(),
    contains=(),
):
    fields = list(fieldnames or [])

    lower = {
        str(name).lower(): name
        for name in fields
        if name is not None
    }

    for key in exact:
        if key.lower() in lower:
            return lower[key.lower()]

    for name in fields:
        lname = str(name).lower()

        if any(
            token.lower() in lname
            for token in contains
        ):
            return name

    return None


def request_csv(grids, start, end):

    params = [
        ("parameters", PARAMETER),
        ("start", start.isoformat()),
        ("end", end.isoformat()),
        ("output_format", "csv"),
    ]

    params.extend(
        (
            "lat_lon",
            (
                f'{grid["lat"]:.6f},'
                f'{grid["lon"]:.6f}'
            ),
        )
        for grid in grids
    )

    last_error = None

    for attempt in range(RETRIES):
        try:
            response = requests.get(
                API,
                params=params,
                timeout=TIMEOUT,
            )

            response.raise_for_status()

            return response.text

        except Exception as exc:
            last_error = exc

            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)

    raise RuntimeError(
        "GeoSphere SPARTACUS request "
        f"failed: {last_error}"
    )


def parse_rows(text):

    reader = csv.DictReader(
        io.StringIO(text)
    )

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

    tx_col = find_column(
        fields,
        exact=("TX",),
        contains=("tx [", "tx[", "maximum"),
    )

    if not all(
        (time_col, lat_col, lon_col, tx_col)
    ):
        raise RuntimeError(
            "Unerwartete SPARTACUS-"
            f"CSV-Spalten: {fields}"
        )

    rows = []

    for row in reader:

        raw_time = row.get(time_col)

        lat = parse_float(row.get(lat_col))
        lon = parse_float(row.get(lon_col))
        tx = parse_float(row.get(tx_col))

        if (
            not raw_time
            or lat is None
            or lon is None
            or tx is None
        ):
            continue

        rows.append(
            (
                str(raw_time)[:10],
                lat,
                lon,
                round(tx, 1),
            )
        )

    return rows


def nearest_grid(lat, lon, grids):

    return min(
        grids,
        key=lambda grid: (
            (grid["lat"] - lat) ** 2
            + (grid["lon"] - lon) ** 2
        ),
    )


def municipality_grids():

    obj = load_json(
        DATA
        / "precipitation"
        / "municipalities.json"
    )

    ix = {
        name: i
        for i, name in enumerate(
            obj["columns"]
        )
    }

    by_state = {}

    for row in obj["municipalities"]:

        state = row[ix["state"]]
        slug = row[ix["state_slug"]]
        grid_id = row[ix["grid_id"]]

        if not state or not slug or not grid_id:
            continue

        by_state.setdefault(
            state,
            {},
        )[grid_id] = {
            "id": grid_id,
            "lat": float(row[ix["grid_lat"]]),
            "lon": float(row[ix["grid_lon"]]),
        }

    result = {}

    for state in STATE_SLUGS:

        unique = by_state.get(state, {})

        result[state] = [
            unique[grid_id]
            for grid_id in sorted(unique)
        ]

    return result


def needs_history(rows, today):
    """
    Prüft die ältere Historie eines Rasterpunkts.

    Die jüngsten 14 Tage werden ohnehin bei
    jedem täglichen Update neu geladen.
    Deshalb prüfen wir hier ausschließlich
    die davor liegenden Tage.

    Neue Rasterpunkte, zu kurze Zeitreihen
    und größere Datenlücken lösen einen
    vollständigen 120-Tage-Abruf aus.
    """

    if not rows:
        return True

    history_start = (
        today - timedelta(days=HISTORY_DAYS)
    )

    history_end = (
        today - timedelta(days=LOOKBACK_DAYS + 1)
    )

    expected_days = (
        history_end - history_start
    ).days + 1

    valid_dates = set()

    for row in rows:

        if len(row) < 2:
            continue

        value = parse_float(row[1])

        if value is None:
            continue

        try:
            day = date.fromisoformat(row[0])
        except (TypeError, ValueError):
            continue

        if history_start <= day <= history_end:
            valid_dates.add(day)

    coverage = (
        len(valid_dates) / expected_days
        if expected_days > 0
        else 1
    )

    return coverage < MIN_HISTORY_COVERAGE


def update_grid_group(
    state,
    grids,
    store,
    start,
    end,
    label,
):

    if not grids:
        return 0

    batches = list(
        chunks(grids, BATCH_SIZE)
    )

    changed = 0

    print(
        f"{state}: {label}, "
        f"{len(grids)} Rasterpunkte, "
        f"{start} bis {end}",
        flush=True,
    )

    for batch_number, batch in enumerate(
        batches,
        start=1,
    ):

        rows = parse_rows(
            request_csv(batch, start, end)
        )

        if not rows:
            raise RuntimeError(
                f"{state}: Keine gültigen "
                "SPARTACUS-Daten erhalten."
            )

        values_by_grid = {
            grid["id"]: {
                row[0]: row[1]
                for row in store.get(
                    grid["id"],
                    [],
                )
            }
            for grid in batch
        }

        received_ids = set()
        resolved = {}

        for day, lat, lon, value in rows:

            if not (
                start.isoformat()
                <= day
                <= end.isoformat()
            ):
                continue

            coordinate = (lat, lon)

            if coordinate not in resolved:

                grid = nearest_grid(
                    lat,
                    lon,
                    batch,
                )

                resolved[coordinate] = grid["id"]

            grid_id = resolved[coordinate]

            received_ids.add(grid_id)

            values = values_by_grid[grid_id]

            if values.get(day) != value:
                changed += 1

            values[day] = value

        missing_ids = {
            grid["id"]
            for grid in batch
        } - received_ids

        if missing_ids:
            raise RuntimeError(
                f"{state}: Keine neuen Daten "
                f"für {len(missing_ids)} "
                "Rasterpunkte erhalten."
            )

        for grid_id, values in (
            values_by_grid.items()
        ):
            store[grid_id] = [
                [day, values[day]]
                for day in sorted(values)
            ]

        print(
            f"{state}: {label}, "
            f"Batch {batch_number}/{len(batches)} "
            "fertig",
            flush=True,
        )

    return changed


def update_state(state, grids, today):

    slug = STATE_SLUGS[state]

    path = (
        TEMP_GRID
        / "current"
        / f"{slug}.json"
    )

    end = today - timedelta(days=1)

    history_start = (
        today - timedelta(days=HISTORY_DAYS)
    )

    update_start = (
        today - timedelta(days=LOOKBACK_DAYS)
    )

    if path.exists():
        obj = load_json(path)
    else:
        obj = {
            "metric": "daily_max_temperature",
            "parameter": PARAMETER,
            "unit": "°C",
            "columns": ["date", "tx"],
            "grids": {},
        }

    valid_grid_ids = {
        grid["id"]
        for grid in grids
    }

    # Veraltete Rasterpunkte entfernen.
    store = {
        grid_id: rows
        for grid_id, rows in (
            obj.get("grids", {}).items()
        )
        if grid_id in valid_grid_ids
    }

    obj["grids"] = store

    history_grids = []
    update_grids = []

    # Historie für jeden Rasterpunkt
    # individuell prüfen.
    for grid in grids:

        grid_id = grid["id"]

        existing_rows = store.get(
            grid_id,
            [],
        )

        if needs_history(existing_rows, today):
            history_grids.append(grid)
        else:
            update_grids.append(grid)

    print()
    print("=" * 64)
    print(f"{state}: {len(grids)} Rasterpunkte")
    print(
        f"120-Tage-Historie: {len(history_grids)}"
    )
    print(
        f"14-Tage-Update: {len(update_grids)}"
    )
    print("=" * 64, flush=True)

    changed = 0

    # Neue Rasterpunkte und unvollständige
    # historische Zeitreihen reparieren.
    changed += update_grid_group(
        state,
        history_grids,
        store,
        history_start,
        end,
        "Historie",
    )

    # Bestehende vollständige Zeitreihen
    # wie bisher ressourcenschonend aktualisieren.
    changed += update_grid_group(
        state,
        update_grids,
        store,
        update_start,
        end,
        "Tagesupdate",
    )

    cutoff = history_start.isoformat()
    end_string = end.isoformat()

    for grid_id in list(store):

        store[grid_id] = [
            row
            for row in store[grid_id]
            if (
                cutoff <= row[0] <= end_string
                and parse_float(row[1]) is not None
            )
        ]

    remaining_gaps = [
        grid["id"]
        for grid in grids
        if needs_history(
            store.get(grid["id"], []),
            today,
        )
    ]

    if remaining_gaps:
        print(
            f"WARNUNG: {state}: "
            f"{len(remaining_gaps)} Rasterpunkte "
            "haben weiterhin historische "
            "Datenlücken.",
            flush=True,
        )

    obj["generated_at"] = today.isoformat()
    obj["history_days"] = HISTORY_DAYS
    obj["grid_count"] = len(store)

    save_json(path, obj)

    print(
        f"{state}: "
        f"{len(store)}/{len(grids)} Rasterpunkte, "
        f"{changed} Werte neu/geändert",
        flush=True,
    )


def run():

    today = datetime.now(
        timezone.utc
    ).astimezone(
        ZoneInfo("Europe/Vienna")
    ).date()

    grids_by_state = municipality_grids()

    total_expected = 0
    total_written = 0

    for state in STATE_SLUGS:

        grids = grids_by_state[state]

        total_expected += len(grids)

        update_state(
            state,
            grids,
            today,
        )

        path = (
            TEMP_GRID
            / "current"
            / f"{STATE_SLUGS[state]}.json"
        )

        if path.exists():

            obj = load_json(path)

            total_written += int(
                obj.get("grid_count", 0)
            )

    print()
    print(
        "Temperature grid current "
        f"update complete: "
        f"{total_written}/{total_expected} grids"
    )

    if total_written != total_expected:
        raise RuntimeError(
            "Temperature grid current "
            "coverage is incomplete: "
            f"{total_written}/{total_expected}"
        )


if __name__ == "__main__":
    run()
