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

# Beim ersten Lauf genug Daten für die 90-Tage-Ansicht laden.
HISTORY_DAYS = 120

# Danach täglich nur die jüngsten Tage erneut laden.
# So können nachträgliche Korrekturen von GeoSphere übernommen werden.
LOOKBACK_DAYS = 14

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
        path.read_text(
            encoding="utf-8"
        )
    )


def save_json(path, obj):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def chunks(items, size):
    for i in range(
        0,
        len(items),
        size,
    ):
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
    except (
        TypeError,
        ValueError,
    ):
        return None

    if not math.isfinite(number):
        return None

    return number


def find_column(
    fieldnames,
    exact=(),
    contains=(),
):
    fields = list(
        fieldnames or []
    )

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


def request_csv(
    grids,
    start,
    end,
):
    params = [
        (
            "parameters",
            PARAMETER,
        ),
        (
            "start",
            start.isoformat(),
        ),
        (
            "end",
            end.isoformat(),
        ),
        (
            "output_format",
            "csv",
        ),
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

    for attempt in range(
        RETRIES
    ):
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

            if (
                attempt
                < RETRIES - 1
            ):
                time.sleep(
                    2 ** attempt
                )

    raise RuntimeError(
        "GeoSphere SPARTACUS request "
        f"failed: {last_error}"
    )


def parse_rows(text):
    reader = csv.DictReader(
        io.StringIO(text)
    )

    fields = (
        reader.fieldnames
        or []
    )

    time_col = find_column(
        fields,
        exact=(
            "time",
            "date",
            "timestamp",
        ),
        contains=(
            "time",
            "date",
        ),
    )

    lat_col = find_column(
        fields,
        exact=(
            "lat",
            "latitude",
        ),
        contains=(
            "latitude",
            "lat",
        ),
    )

    lon_col = find_column(
        fields,
        exact=(
            "lon",
            "longitude",
        ),
        contains=(
            "longitude",
            "lon",
        ),
    )

    tx_col = find_column(
        fields,
        exact=("TX",),
        contains=(
            "tx [",
            "tx[",
            "maximum",
        ),
    )

    if (
        not time_col
        or not lat_col
        or not lon_col
        or not tx_col
    ):
        raise RuntimeError(
            "Unerwartete SPARTACUS-"
            f"CSV-Spalten: {fields}"
        )

    rows = []

    for row in reader:
        raw_time = row.get(
            time_col
        )

        lat = parse_float(
            row.get(lat_col)
        )

        lon = parse_float(
            row.get(lon_col)
        )

        tx = parse_float(
            row.get(tx_col)
        )

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


def nearest_grid(
    lat,
    lon,
    grids,
):
    return min(
        grids,
        key=lambda grid: (
            (grid["lat"] - lat) ** 2
            +
            (grid["lon"] - lon) ** 2
        ),
    )


def municipality_grids():
    obj = load_json(
        DATA
        / "precipitation"
        / "municipalities.json"
    )

    columns = obj["columns"]

    ix = {
        name: i
        for i, name
        in enumerate(columns)
    }

    by_state = {}

    for row in obj[
        "municipalities"
    ]:
        state = row[
            ix["state"]
        ]

        slug = row[
            ix["state_slug"]
        ]

        grid_id = row[
            ix["grid_id"]
        ]

        if (
            not state
            or not slug
            or not grid_id
        ):
            continue

        by_state.setdefault(
            state,
            {},
        )[grid_id] = {
            "id": grid_id,
            "lat": float(
                row[ix["grid_lat"]]
            ),
            "lon": float(
                row[ix["grid_lon"]]
            ),
        }

    result = {}

    for state in STATE_SLUGS:
        unique = by_state.get(
            state,
            {},
        )

        result[state] = [
            unique[grid_id]
            for grid_id
            in sorted(unique)
        ]

    return result


def existing_start_date(
    path,
    today,
):
    if not path.exists():
        return (
            today
            - timedelta(
                days=HISTORY_DAYS
            )
        )

    return (
        today
        - timedelta(
            days=LOOKBACK_DAYS
        )
    )


def update_state(
    state,
    grids,
    today,
):
    slug = STATE_SLUGS[state]

    path = (
        TEMP_GRID
        / "current"
        / f"{slug}.json"
    )

    start = existing_start_date(
        path,
        today,
    )

    # Nur abgeschlossene Tage.
    end = (
        today
        - timedelta(days=1)
    )

    if start > end:
        return

    if path.exists():
        obj = load_json(path)
    else:
        obj = {
            "metric": (
                "daily_max_temperature"
            ),
            "parameter": PARAMETER,
            "unit": "°C",
            "columns": [
                "date",
                "tx",
            ],
            "grids": {},
        }

    store = obj.setdefault(
        "grids",
        {},
    )

    valid_grid_ids = {
        grid["id"]
        for grid in grids
    }

    store = {
        grid_id: rows
        for grid_id, rows in store.items()
        if grid_id in valid_grid_ids
    }

    obj["grids"] = store

    print()
    print(
        f"{state}: "
        f"{len(grids)} Rasterpunkte, "
        f"{start} bis {end}"
    )

    changed = 0

    for batch_number, batch in enumerate(
        chunks(
            grids,
            BATCH_SIZE,
        ),
        start=1,
    ):
        rows = parse_rows(
            request_csv(
                batch,
                start,
                end,
            )
        )

        for (
            day,
            lat,
            lon,
            value,
        ) in rows:
            grid = nearest_grid(
                lat,
                lon,
                batch,
            )

            grid_id = grid["id"]

            existing_rows = (
                store.get(
                    grid_id,
                    [],
                )
            )

            values = {
                row[0]: row[1]
                for row
                in existing_rows
            }

            if (
                values.get(day)
                != value
            ):
                changed += 1

            values[day] = value

            store[grid_id] = [
                [
                    date_string,
                    values[
                        date_string
                    ],
                ]
                for date_string
                in sorted(values)
            ]

        print(
            f"{state}: Batch "
            f"{batch_number} fertig"
        )

    # Nur den Zeitraum behalten,
    # den der 90-Tage-Chart benötigt,
    # plus Sicherheitsreserve.
    cutoff = (
        today
        - timedelta(
            days=HISTORY_DAYS
        )
    ).isoformat()

    for grid_id in list(
        store
    ):
        store[grid_id] = [
            row
            for row
            in store[grid_id]
            if row[0] >= cutoff
        ]

    obj[
        "generated_at"
    ] = today.isoformat()

    obj[
        "history_days"
    ] = HISTORY_DAYS

    obj[
        "grid_count"
    ] = len(store)

    save_json(
        path,
        obj,
    )

    print(
        f"{state}: "
        f"{len(store)}/"
        f"{len(grids)} Rasterpunkte, "
        f"{changed} Werte neu/geändert"
    )


def run():
    today = datetime.now(
        timezone.utc
    ).astimezone(
        ZoneInfo("Europe/Vienna")
    ).date()

    grids_by_state = (
        municipality_grids()
    )

    total_expected = 0
    total_written = 0

    for state in STATE_SLUGS:
        grids = grids_by_state[
            state
        ]

        total_expected += len(
            grids
        )

        update_state(
            state,
            grids,
            today,
        )

        path = (
            TEMP_GRID
            / "current"
            / (
                STATE_SLUGS[
                    state
                ]
                + ".json"
            )
        )

        if path.exists():
            obj = load_json(
                path
            )

            total_written += int(
                obj.get(
                    "grid_count",
                    0,
                )
            )

    print()
    print(
        "Temperature grid current "
        f"update complete: "
        f"{total_written}/"
        f"{total_expected} grids"
    )

    if (
        total_written
        != total_expected
    ):
        raise RuntimeError(
            "Temperature grid current "
            "coverage is incomplete: "
            f"{total_written}/"
            f"{total_expected}"
        )


if __name__ == "__main__":
    run()
