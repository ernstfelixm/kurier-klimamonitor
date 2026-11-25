
from __future__ import annotations

import argparse
import calendar
import json
import math
import statistics
import time

from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import build_temperature_grid as temperature
import build_precipitation as precipitation
import build_snow_climatology as snow


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"

HISTORY_START = date(1961, 1, 1)
VIENNA = ZoneInfo("Europe/Vienna")

STATE_SLUGS = list(temperature.STATE_SLUGS.values())

CONFIG = {
    "temperature": {
        "folder": "temperature_grid",
        "mapping": "precipitation",
        "module": temperature,
        "batch": 30,
        "metrics": ["tx_mean"],
        "source": "SPARTACUS v3",
        "parameter": "TX",
    },
    "precipitation": {
        "folder": "precipitation",
        "mapping": "precipitation",
        "module": precipitation,
        "batch": 30,
        "metrics": ["rr_sum"],
        "source": "SPARTACUS v3",
        "parameter": "RR",
    },
    "snow": {
        "folder": "snow",
        "mapping": "snow",
        "module": snow,
        "batch": 15,
        "metrics": [
            "snow_days",
            "snow_mean",
            "snow_max",
        ],
        "source": "SNOWGRID Klima v2.1",
        "parameter": "snow_depth",
    },
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


def valid_number(value, metric):
    if value is None:
        return False

    if not math.isfinite(value):
        return False

    if metric != "temperature" and value < 0:
        return False

    return True


def month_start(day):
    return date(day.year, day.month, 1)


def month_end(day):
    return date(
        day.year,
        day.month,
        calendar.monthrange(
            day.year,
            day.month,
        )[1],
    )


def next_month(day):
    if day.month == 12:
        return date(day.year + 1, 1, 1)

    return date(day.year, day.month + 1, 1)


def iter_months(start, end):
    current = month_start(start)

    while current <= end:
        yield current
        current = next_month(current)


def iter_days(start, end):
    current = start

    while current <= end:
        yield current
        current += timedelta(days=1)


def mapping_for_state(config, state_slug):

    path = (
        DATA
        / config["mapping"]
        / "municipalities.json"
    )

    obj = load_json(path)

    ix = {
        name: i
        for i, name in enumerate(obj["columns"])
    }

    grids = {}

    for row in obj["municipalities"]:

        if row[ix["state_slug"]] != state_slug:
            continue

        gid = str(row[ix["grid_id"]])

        grids[gid] = {
            "grid_id": gid,
            "lat": float(row[ix["grid_lat"]]),
            "lon": float(row[ix["grid_lon"]]),
        }

    if not grids:
        raise RuntimeError(
            f"Keine Rasterpunkte für {state_slug}"
        )

    return list(grids.values())


def fetch_daily(metric, config, grids, start, end):

    module = config["module"]

    by_grid = {
        g["grid_id"]: {}
        for g in grids
    }

    number_days = (end - start).days + 1

    batch_size = min(
        config["batch"],
        max(1, 800000 // number_days),
    )

    batches = list(chunks(grids, batch_size))

    for i, batch in enumerate(batches, start=1):

        print(
            f"{metric}: Batch {i}/{len(batches)}, "
            f"{len(batch)} Rasterpunkte, "
            f"{start} bis {end}",
            flush=True,
        )

        coordinates = [
            (g["lat"], g["lon"])
            for g in batch
        ]

        coordinate_ids = {
            (g["lat"], g["lon"]): g["grid_id"]
            for g in batch
        }

        rows = module.parse_rows(
            module.request_csv(
                coordinates,
                start,
                end,
            )
        )

        if not rows:
            raise RuntimeError(
                "GeoSphere lieferte keine Daten."
            )

        resolved = {}

        for raw_day, lat, lon, value in rows:

            try:
                day = date.fromisoformat(raw_day)
            except ValueError:
                continue

            if not start <= day <= end:
                continue

            key = (lat, lon)

            if key not in resolved:

                nearest = min(
                    coordinates,
                    key=lambda p:
                        (p[0] - lat) ** 2
                        + (p[1] - lon) ** 2,
                )

                distance = math.hypot(
                    nearest[0] - lat,
                    nearest[1] - lon,
                )

                if distance > 0.002:
                    raise RuntimeError(
                        "Unerwartete Rasterkoordinate: "
                        f"{lat}, {lon}"
                    )

                resolved[key] = coordinate_ids[
                    nearest
                ]

            gid = resolved[key]

            if valid_number(value, metric):
                by_grid[gid][day] = value

        time.sleep(1)

    return by_grid


def aggregate_month(metric, daily, month, cutoff):

    last_day = month_end(month)

    available_end = min(
        cutoff,
        last_day,
    )

    days_expected = last_day.day

    values = []

    for day in iter_days(month, available_end):

        value = daily.get(day)

        if valid_number(value, metric):
            values.append(value)
        else:
            # Keine unbemerkten Lücken innerhalb
            # der verwendeten Tagesreihe.
            break

    count = len(values)

    complete = (
        count == days_expected
        and last_day <= cutoff
    )

    month_key = month.strftime("%Y-%m")

    # Abgeschlossene Monate mit Datenlücken
    # werden nicht als gültige Monate verwendet.
    if count == 0 or (
        last_day <= cutoff
        and not complete
        and month != month_start(cutoff)
    ):
        return None

    if metric == "temperature":
        metrics = [
            round(statistics.mean(values), 2)
        ]

    elif metric == "precipitation":
        metrics = [
            round(math.fsum(values), 2)
        ]

    elif metric == "snow":
        metrics = [
            sum(v >= 1 for v in values),
            round(statistics.mean(values), 2),
            round(max(values), 2),
        ]

    return [
        month_key,
        *metrics,
        count,
        days_expected,
        complete,
    ]


def latest_valid_day(daily, cutoff):

    valid = [
        day
        for day in daily
        if day <= cutoff
    ]

    return max(valid) if valid else None


def output_path(config, state_slug):

    return (
        DATA
        / config["folder"]
        / "monthly"
        / f"{state_slug}.json"
    )


def build_state(metric, state_slug, mode):

    config = CONFIG[metric]

    path = output_path(
        config,
        state_slug,
    )

    grids = mapping_for_state(
        config,
        state_slug,
    )

    expected_ids = {
        g["grid_id"]
        for g in grids
    }

    today = datetime.now(VIENNA).date()

    cutoff = today - timedelta(days=1)

    existing = {}

    if mode == "update":

        if not path.exists():
            print(
                f"Überspringe {metric}/{state_slug}: "
                "Historie noch nicht aufgebaut."
            )
            return

        existing = load_json(path)

        if set(existing["grids"]) != expected_ids:
            raise RuntimeError(
                "Rasterzuordnung wurde verändert. "
                "Bitte historischen Build wiederholen: "
                f"{metric}/{state_slug}"
            )

        # Auch nachträgliche Korrekturen der
        # vergangenen Monate übernehmen.
        start = month_start(
            cutoff - timedelta(days=120)
        )

    else:
        start = HISTORY_START

    print(
        f"\n{metric.upper()} – {state_slug}: "
        f"{len(grids)} Rasterpunkte\n",
        flush=True,
    )

    compact_grids = {}
    data_stands = {}
    incomplete_history = 0

    number_days = (cutoff - start).days + 1

    batch_size = min(
        config["batch"],
        max(1, 800000 // number_days),
    )

    batches = list(chunks(grids, batch_size))

    for batch_index, batch in enumerate(
        batches,
        start=1,
    ):

        print(
            f"Verarbeite Gruppe "
            f"{batch_index}/{len(batches)}",
            flush=True,
        )

        history = fetch_daily(
            metric,
            config,
            batch,
            start,
            cutoff,
        )

        for grid in batch:

            gid = grid["grid_id"]
            daily = history[gid]

            latest = latest_valid_day(
                daily,
                cutoff,
            )

            if latest is None:
                raise RuntimeError(
                    f"Keine gültigen Daten für {gid}"
                )

            # Verhindert, dass ein unerwartet alter
            # Datenstand publiziert wird.
            if (cutoff - latest).days > 14:
                raise RuntimeError(
                    f"Veralteter Datenstand für "
                    f"{gid}: {latest}"
                )

            data_stands[gid] = latest.isoformat()

            rows = []

            if existing:
                rows = [
                    row
                    for row in existing["grids"][gid]
                    if row[0] < start.strftime("%Y-%m")
                ]

            for month in iter_months(start, latest):

                row = aggregate_month(
                    metric,
                    daily,
                    month,
                    latest,
                )

                if row is not None:
                    rows.append(row)

                elif month_end(month) <= latest:
                    incomplete_history += 1

            if not rows:
                raise RuntimeError(
                    f"Keine Monatswerte für {gid}"
                )

            compact_grids[gid] = rows

        del history

    if set(compact_grids) != expected_ids:
        raise RuntimeError(
            "Nicht alle Rasterpunkte wurden verarbeitet."
        )

    result = {
        "schema_version": 1,
        "metric": metric,
        "source": (
            "GeoSphere Austria "
            + config["source"]
        ),
        "parameter": config["parameter"],
        "history_start": HISTORY_START.isoformat(),
        "generated_at": (
            datetime.now(VIENNA).isoformat()
        ),
        "reference_periods": [
            "1961-1990",
            "1991-2020",
        ],
        "snow_day_threshold_cm": (
            1 if metric == "snow" else None
        ),
        "columns": [
            "month",
            *config["metrics"],
            "days_valid",
            "days_expected",
            "complete",
        ],
        "grid_count": len(compact_grids),
        "incomplete_months_in_build": (
            incomplete_history
        ),
        "data_stands": data_stands,
        "grids": compact_grids,
    }

    save_json(path, result)

    print(
        f"Fertig: {path}\n"
        f"Rasterpunkte: {len(compact_grids)}\n"
        f"Historische Monatslücken: "
        f"{incomplete_history}",
        flush=True,
    )


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--metric",
        choices=["all", *CONFIG],
        default="all",
    )

    parser.add_argument(
        "--state",
        choices=["all", *STATE_SLUGS],
        default="all",
    )

    parser.add_argument(
        "--mode",
        choices=["build", "update"],
        default="build",
    )

    args = parser.parse_args()

    metrics = (
        list(CONFIG)
        if args.metric == "all"
        else [args.metric]
    )

    states = (
        STATE_SLUGS
        if args.state == "all"
        else [args.state]
    )

    for metric in metrics:
        for state_slug in states:

            build_state(
                metric,
                state_slug,
                args.mode,
            )

    print(
        "\nMonatsdaten erfolgreich verarbeitet.",
        flush=True,
    )


if __name__ == "__main__":
    main()
