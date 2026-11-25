
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import math


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"

VIENNA = ZoneInfo("Europe/Vienna")

STATES = [
    "burgenland",
    "kaernten",
    "niederoesterreich",
    "oberoesterreich",
    "salzburg",
    "steiermark",
    "tirol",
    "vorarlberg",
    "wien",
]

CONFIG = {
    "temperature": {
        "folder": "temperature_grid",
        "indicator": "tx_mean",
        "unit": "°C",
    },
    "precipitation": {
        "folder": "precipitation",
        "indicator": "rr_sum",
        "unit": "mm",
    },
    "snow": {
        "folder": "snow",
        "indicator": "snow_days",
        "unit": "Tage",
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


def valid_number(value):

    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def linear_trend(values):

    if len(values) < 3:
        return None

    values = sorted(values)

    n = len(values)

    mean_x = sum(
        year for year, value in values
    ) / n

    mean_y = sum(
        value for year, value in values
    ) / n

    denominator = sum(
        (year - mean_x) ** 2
        for year, value in values
    )

    if denominator == 0:
        return None

    slope = sum(
        (year - mean_x)
        * (value - mean_y)
        for year, value in values
    ) / denominator

    first_year = values[0][0]
    last_year = values[-1][0]

    change = slope * (
        last_year - first_year
    )

    return [
        first_year,
        last_year,
        round(change, 8),
        n,
    ]


def monthly_trends(
    rows,
    columns,
    indicator,
    kind,
):

    value_index = columns.index(indicator)
    valid_index = columns.index("days_valid")
    expected_index = columns.index("days_expected")
    complete_index = columns.index("complete")

    buckets = {
        month: []
        for month in range(1, 13)
    }

    for row in rows:

        if row[complete_index] is not True:
            continue

        if row[valid_index] != row[expected_index]:
            continue

        if row[valid_index] <= 0:
            continue

        value = row[value_index]

        if not valid_number(value):
            continue

        if kind != "temperature" and value < 0:
            continue

        try:
            year = int(row[0][:4])
            month = int(row[0][5:7])

        except (ValueError, TypeError):
            continue

        if year < 1961:
            continue

        if month not in buckets:
            continue

        buckets[month].append(
            (year, value)
        )

    return [
        linear_trend(buckets[month])
        for month in range(1, 13)
    ]


def build_file(kind, config, state):

    source_path = (
        DATA
        / config["folder"]
        / "monthly"
        / f"{state}.json"
    )

    if not source_path.exists():
        raise RuntimeError(
            f"Monatsdatei fehlt: {source_path}"
        )

    obj = load_json(source_path)

    if obj.get("schema_version") != 1:
        raise RuntimeError(
            f"Unbekannte Schema-Version: "
            f"{source_path}"
        )

    if obj.get("metric") != kind:
        raise RuntimeError(
            f"Unerwartete Kennzahl: "
            f"{source_path}"
        )

    columns = obj["columns"]

    grids = {}

    for grid_id, rows in obj["grids"].items():

        trends = monthly_trends(
            rows,
            columns,
            config["indicator"],
            kind,
        )

        if len(trends) != 12:
            raise RuntimeError(
                f"Ungültige Monatsanzahl: {grid_id}"
            )

        if any(trend is None for trend in trends):
            raise RuntimeError(
                f"Unzureichende historische Daten: "
                f"{kind}/{state}/{grid_id}"
            )

        grids[grid_id] = trends

    if len(grids) != obj["grid_count"]:
        raise RuntimeError(
            f"Inkonsistente Rasteranzahl: "
            f"{kind}/{state}"
        )

    result = {
        "schema_version": 1,
        "metric": kind,
        "indicator": config["indicator"],
        "unit": config["unit"],
        "source": obj.get("source"),
        "source_generated_at": obj.get(
            "generated_at"
        ),
        "generated_at": datetime.now(
            VIENNA
        ).isoformat(),
        "method": "linear_least_squares",
        "complete_months_only": True,
        "month_order": list(range(1, 13)),
        "columns": [
            "first_year",
            "last_year",
            "trend_change",
            "n",
        ],
        "grid_count": len(grids),
        "grids": grids,
    }

    output_path = (
        DATA
        / config["folder"]
        / "monthly"
        / "teasers"
        / f"{state}.json"
    )

    save_json(output_path, result)

    size_kb = round(
        output_path.stat().st_size / 1000,
        1,
    )

    print(
        f"{kind}/{state}: "
        f"{len(grids)} Rasterpunkte | "
        f"{len(grids) * 12} Trends | "
        f"{size_kb} kB",
        flush=True,
    )

    return len(grids), size_kb


def main():

    total_files = 0
    total_grids = 0
    total_size = 0

    for kind, config in CONFIG.items():

        for state in STATES:

            grids, size = build_file(
                kind,
                config,
                state,
            )

            total_files += 1
            total_grids += grids
            total_size += size

    print()
    print("=" * 60)
    print("PREMIUM-TEASER ERFOLGREICH ERZEUGT")
    print("=" * 60)
    print(f"Dateien: {total_files}")
    print(f"Raster-Zeitreihen: {total_grids}")
    print(f"Monatstrends: {total_grids * 12}")
    print(f"Dateigröße gesamt: {total_size:.1f} kB")


if __name__ == "__main__":
    main()
