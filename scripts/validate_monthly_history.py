
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from collections import Counter
import calendar
import json
import math
import sys


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"

TODAY = datetime.now(
    ZoneInfo("Europe/Vienna")
).date()

CUTOFF = TODAY - timedelta(days=1)

START_YEAR = 1961
MIN_REFERENCE_YEARS = 24

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
        "mapping": "precipitation",
        "metrics": ["tx_mean"],
    },
    "precipitation": {
        "folder": "precipitation",
        "mapping": "precipitation",
        "metrics": ["rr_sum"],
    },
    "snow": {
        "folder": "snow",
        "mapping": "snow",
        "metrics": [
            "snow_days",
            "snow_mean",
            "snow_max",
        ],
    },
}

ERRORS = []
WARNINGS = []
RESULTS = []


def load_json(path):
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def error(message):
    ERRORS.append(message)


def warning(message):
    WARNINGS.append(message)


def valid_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def valid_integer(value):
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
    )


def month_keys(start_year, end):

    result = []

    for year in range(start_year, end.year + 1):
        for month in range(1, 13):

            if (year, month) > (end.year, end.month):
                break

            result.append(
                f"{year:04d}-{month:02d}"
            )

    return result


def expected_mapping(folder, state):

    path = DATA / folder / "municipalities.json"

    obj = load_json(path)

    ix = {
        name: i
        for i, name in enumerate(obj["columns"])
    }

    grids = set()
    municipalities = set()

    for row in obj["municipalities"]:

        if row[ix["state_slug"]] != state:
            continue

        municipalities.add(
            str(row[ix["id"]])
        )

        gid = row[ix["grid_id"]]

        if not gid:
            error(
                f"{folder}/{state}: "
                "Gemeinde ohne Raster-ID"
            )
        else:
            grids.add(str(gid))

    return grids, municipalities


def validate_file(kind, config, state):

    label = f"{kind}/{state}"

    path = (
        DATA
        / config["folder"]
        / "monthly"
        / f"{state}.json"
    )

    result = {
        "metric": kind,
        "state": state,
        "municipalities": 0,
        "expected_grids": 0,
        "actual_grids": 0,
        "complete_months": 0,
        "provisional_months": 0,
        "missing_months": 0,
        "reference_gaps": 0,
        "reference_insufficient": 0,
        "invalid_rows": 0,
        "oldest_data_stand": None,
        "latest_data_stand": None,
        "size_mb": 0,
        "missing_month_frequency": {},
        "status": "OK",
    }

    before_errors = len(ERRORS)
    before_warnings = len(WARNINGS)

    def finish():

        if len(ERRORS) > before_errors:
            result["status"] = "ERROR"

        elif len(WARNINGS) > before_warnings:
            result["status"] = "WARNING"

        RESULTS.append(result)

        print(
            f"{label}: {result['status']} | "
            f"Raster: {result['actual_grids']}/"
            f"{result['expected_grids']} | "
            f"Monatslücken: "
            f"{result['missing_months']} | "
            f"Referenzlücken: "
            f"{result['reference_gaps']}",
            flush=True,
        )

    expected, municipalities = expected_mapping(
        config["mapping"],
        state,
    )

    result["expected_grids"] = len(expected)
    result["municipalities"] = len(municipalities)

    if not expected:
        error(f"{label}: Keine Rasterzuordnung")

    if not path.exists():
        error(f"{label}: Monatsdatei fehlt")
        finish()
        return

    result["size_mb"] = round(
        path.stat().st_size / 1_000_000,
        3,
    )

    obj = load_json(path)

    if obj.get("schema_version") != 1:
        error(f"{label}: Unerwartete Schema-Version")

    if obj.get("metric") != kind:
        error(f"{label}: Falsche Kennzahl")

    columns = obj.get("columns", [])

    required = [
        "month",
        *config["metrics"],
        "days_valid",
        "days_expected",
        "complete",
    ]

    if any(c not in columns for c in required):
        error(f"{label}: Fehlende Datenspalten")
        finish()
        return

    ix = {
        name: i
        for i, name in enumerate(columns)
    }

    grids = obj.get("grids", {})
    actual = set(grids)

    result["actual_grids"] = len(actual)

    if obj.get("grid_count") != len(actual):
        error(f"{label}: Inkonsistenter grid_count")

    missing_grids = expected - actual
    extra_grids = actual - expected

    if missing_grids:
        error(
            f"{label}: {len(missing_grids)} "
            f"Rasterpunkte fehlen: "
            f"{sorted(missing_grids)[:10]}"
        )

    if extra_grids:
        error(
            f"{label}: {len(extra_grids)} "
            "nicht mehr zugeordnete Rasterpunkte"
        )

    data_stands = obj.get("data_stands", {})

    missing_frequency = Counter()
    valid_stands = []

    for gid, rows in grids.items():

        prefix = f"{label}/{gid}"

        if not isinstance(rows, list) or not rows:
            error(f"{prefix}: Keine Monatswerte")
            continue

        try:
            stand = date.fromisoformat(
                data_stands[gid]
            )

        except (KeyError, TypeError, ValueError):
            error(f"{prefix}: Ungültiger Datenstand")
            continue

        valid_stands.append(stand)

        if stand > CUTOFF:
            error(f"{prefix}: Datenstand in Zukunft")

        age = (CUTOFF - stand).days

        if age > 14:
            error(
                f"{prefix}: Datenstand "
                f"{age} Tage verspätet"
            )

        elif age > 3:
            warning(
                f"{prefix}: Datenstand "
                f"{age} Tage verspätet"
            )

        seen = set()
        complete_keys = set()

        reference_counts = {
            1961: Counter(),
            1991: Counter(),
        }

        previous_key = None

        for row in rows:

            reasons = []

            if (
                not isinstance(row, list)
                or len(row) != len(columns)
            ):
                result["invalid_rows"] += 1
                error(f"{prefix}: Ungültige Zeile")
                continue

            key = row[ix["month"]]

            try:
                if (
                    not isinstance(key, str)
                    or len(key) != 7
                ):
                    raise ValueError()

                month_date = date.fromisoformat(
                    key + "-01"
                )

                year = month_date.year
                month = month_date.month

            except (TypeError, ValueError):
                result["invalid_rows"] += 1
                error(f"{prefix}: Ungültiger Monat")
                continue

            if year < START_YEAR:
                reasons.append(
                    "Monat vor historischem Beginn"
                )

            if month_date > stand:
                reasons.append(
                    "Monat nach Datenstand"
                )

            if key in seen:
                reasons.append("Doppelter Monat")

            if previous_key and key < previous_key:
                reasons.append("Falsche Sortierung")

            previous_key = key
            seen.add(key)

            days = row[ix["days_valid"]]
            expected_days = row[ix["days_expected"]]
            complete = row[ix["complete"]]

            calendar_days = calendar.monthrange(
                year,
                month,
            )[1]

            days_ok = (
                valid_integer(days)
                and valid_integer(expected_days)
                and 0 < days <= expected_days
                and expected_days == calendar_days
            )

            if not days_ok:
                reasons.append(
                    "Ungültige Tagesanzahl"
                )

            elif (
                year == stand.year
                and month == stand.month
                and days > stand.day
            ):
                reasons.append(
                    "Tagesanzahl nach Datenstand"
                )

            if not isinstance(complete, bool):
                reasons.append(
                    "Ungültiger Vollständigkeitsstatus"
                )

            elif days_ok and complete != (
                days == expected_days
            ):
                reasons.append(
                    "Inkonsistenter Vollständigkeitsstatus"
                )

            values = {
                metric: row[ix[metric]]
                for metric in config["metrics"]
            }

            numbers_ok = all(
                valid_number(value)
                for value in values.values()
            )

            if not numbers_ok:
                reasons.append("Ungültiger Zahlenwert")

            else:

                if kind != "temperature" and any(
                    v < 0 for v in values.values()
                ):
                    reasons.append(
                        "Negativer Niederschlags- "
                        "oder Schneewert"
                    )

                if kind == "snow":

                    snow_days = values["snow_days"]
                    snow_mean = values["snow_mean"]
                    snow_max = values["snow_max"]

                    if (
                        not valid_integer(snow_days)
                        or (
                            days_ok and snow_days > days
                        )
                    ):
                        reasons.append(
                            "Ungültige Schneetage"
                        )

                    if snow_mean > snow_max:
                        reasons.append(
                            "Mittlere Schneehöhe "
                            "größer als Maximum"
                        )

                    if snow_days > 0 and snow_max < 1:
                        reasons.append(
                            "Schneetage trotz "
                            "Schneehöhe unter 1 cm"
                        )

            if reasons:
                result["invalid_rows"] += 1
                error(
                    f"{prefix}/{key}: "
                    + "; ".join(reasons)
                )
                continue

            if complete:

                complete_keys.add(key)
                result["complete_months"] += 1

                if 1961 <= year <= 1990:
                    reference_counts[1961][month] += 1

                elif 1991 <= year <= 2020:
                    reference_counts[1991][month] += 1

            else:

                result["provisional_months"] += 1

                if (
                    year != stand.year
                    or month != stand.month
                ):
                    error(
                        f"{prefix}/{key}: "
                        "Historischer Monat "
                        "als vorläufig gespeichert"
                    )

        # Nur bereits abgeschlossene Monate
        # bis zum tatsächlichen Datenstand prüfen.
        expected_months = set(
            month_keys(START_YEAR, stand)
        )

        if stand.day < calendar.monthrange(
            stand.year,
            stand.month,
        )[1]:

            expected_months.discard(
                stand.strftime("%Y-%m")
            )

        missing = expected_months - complete_keys

        result["missing_months"] += len(missing)

        missing_frequency.update(missing)

        # Referenzperioden getrennt für
        # jeden Kalendermonat überprüfen.
        for reference_start in (1961, 1991):

            for month in range(1, 13):

                count = reference_counts[
                    reference_start
                ][month]

                if count < 30:

                    result["reference_gaps"] += (
                        30 - count
                    )

                if count < MIN_REFERENCE_YEARS:

                    result[
                        "reference_insufficient"
                    ] += 1

                    error(
                        f"{prefix}: Referenz "
                        f"{reference_start}-"
                        f"{reference_start + 29}, "
                        f"Monat {month:02d}: "
                        f"nur {count}/30 Jahre"
                    )

    if valid_stands:

        result["oldest_data_stand"] = (
            min(valid_stands).isoformat()
        )

        result["latest_data_stand"] = (
            max(valid_stands).isoformat()
        )

    result["missing_month_frequency"] = dict(
        sorted(missing_frequency.items())
    )

    if result["missing_months"]:
        warning(
            f"{label}: "
            f"{result['missing_months']} "
            "fehlende historische Raster-Monatswerte"
        )

    if result["reference_gaps"]:
        warning(
            f"{label}: "
            f"{result['reference_gaps']} "
            "fehlende Referenzwerte"
        )

    finish()


def write_report():

    summary_path = (
        ROOT / "monthly_quality_report.md"
    )

    report = [
        "# KURIER Klima-Monitor",
        "",
        "## Qualitätsprüfung der Monatsdaten",
        "",
        f"Prüfdatum: {TODAY.isoformat()}",
        "",
        f"- Geprüfte Dateien: {len(RESULTS)}/27",
        f"- Fehler: {len(ERRORS)}",
        f"- Warnungen: {len(WARNINGS)}",
        "",
        "| Monitor | Bundesland | Raster Soll | "
        "Raster Ist | Vollständige Monate | "
        "Vorläufig | Lücken | Referenzlücken | "
        "Datenstand ältester | MB | Status |",
        "|---|---|---:|---:|---:|---:|---:|"
        "---:|---|---:|---|",
    ]

    for r in RESULTS:

        report.append(
            f"| {r['metric']} "
            f"| {r['state']} "
            f"| {r['expected_grids']} "
            f"| {r['actual_grids']} "
            f"| {r['complete_months']} "
            f"| {r['provisional_months']} "
            f"| {r['missing_months']} "
            f"| {r['reference_gaps']} "
            f"| {r['oldest_data_stand'] or '-'} "
            f"| {r['size_mb']} "
            f"| {r['status']} |"
        )

    report.extend([
        "",
        "## Historische Datenlücken",
        "",
    ])

    for r in RESULTS:

        gaps = r["missing_month_frequency"]

        if not gaps:
            continue

        report.append(
            f"### {r['metric']} / {r['state']}"
        )

        report.append("")

        for month, count in gaps.items():

            report.append(
                f"- {month}: "
                f"{count} Rasterpunkte"
            )

        report.append("")

    report.extend([
        "",
        "## Fehler",
        "",
    ])

    report.extend(
        [f"- {x}" for x in ERRORS[:100]]
        if ERRORS
        else ["Keine Fehler."]
    )

    report.extend([
        "",
        "## Warnungen",
        "",
    ])

    report.extend(
        [f"- {x}" for x in WARNINGS[:100]]
        if WARNINGS
        else ["Keine Warnungen."]
    )

    summary_path.write_text(
        "\n".join(report),
        encoding="utf-8",
    )

    json_path = ROOT / "monthly_quality_report.json"

    json_path.write_text(
        json.dumps(
            {
                "checked_at": datetime.now(
                    ZoneInfo("Europe/Vienna")
                ).isoformat(),
                "files_checked": len(RESULTS),
                "error_count": len(ERRORS),
                "warning_count": len(WARNINGS),
                "results": RESULTS,
                "errors": ERRORS,
                "warnings": WARNINGS,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main():

    for kind, config in CONFIG.items():

        for state in STATES:

            try:
                validate_file(
                    kind,
                    config,
                    state,
                )

            except Exception as exc:

                error(
                    f"{kind}/{state}: "
                    f"Prüfung fehlgeschlagen: {exc}"
                )

                print(
                    ERRORS[-1],
                    flush=True,
                )

    write_report()

    print()
    print("=" * 70)
    print("QUALITÄTSPRÜFUNG ABGESCHLOSSEN")
    print("=" * 70)
    print(f"Geprüfte Dateien: {len(RESULTS)}/27")
    print(f"Fehler: {len(ERRORS)}")
    print(f"Warnungen: {len(WARNINGS)}")
    print()

    if ERRORS or len(RESULTS) != 27:
        sys.exit(1)

    print("Qualitätsprüfung bestanden.")


if __name__ == "__main__":
    main()
