from pathlib import Path
from datetime import timedelta, datetime, timezone
from zoneinfo import ZoneInfo
import csv
import io
import json
import math
import time

import requests

BASE = "https://dataset.api.hub.geosphere.at/v1/station/historical/klima-v2-1d"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"

LOOKBACK_DAYS = 10
CHUNK_SIZE = 80
TIMEOUT = 90


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def fetch_chunk(ids, start, end):
    params = [
        ("parameters", "tlmax"),
        ("start", start.isoformat()),
        ("end", end.isoformat()),
        ("output_format", "csv"),
    ]
    params += [("station_ids", str(x)) for x in ids]

    last_error = None
    for attempt in range(4):
        try:
            response = requests.get(BASE, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last_error = exc
            time.sleep(2 ** attempt)

    raise RuntimeError(
        f"GeoSphere request failed for {len(ids)} stations: {last_error}"
    )


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    out = []

    for row in reader:
        sid = row.get("station") or row.get("station_id")
        raw_date = row.get("time") or row.get("date")
        raw_val = row.get("tlmax") or row.get("tlmax_c")

        if not sid or not raw_date or raw_val in (None, "", "nan", "NaN"):
            continue

        try:
            value = float(str(raw_val).replace(",", "."))
            if not math.isfinite(value):
                continue

            day = raw_date[:10]
            out.append((str(int(float(sid))), day, round(value, 1)))
        except (ValueError, TypeError):
            continue

    return out


def now_vienna():
    vienna = ZoneInfo("Europe/Vienna")
    return datetime.now(timezone.utc).astimezone(vienna)


def used_station_ids():
    """Return only stations that are actually used by the municipality search."""
    municipalities = load_json(DATA / "municipalities.json")
    columns = municipalities.get("columns", [])
    try:
        station_index = columns.index("station_id")
    except ValueError as exc:
        raise RuntimeError("municipalities.json has no station_id column") from exc

    ids = {
        int(row[station_index])
        for row in municipalities.get("municipalities", [])
        if row[station_index] not in (None, "")
    }
    return ids


def latest_existing_observation():
    status_path = DATA / "status.json"
    if not status_path.exists():
        return None
    return load_json(status_path).get("latest_observation")


def run_update():
    stations_obj = load_json(DATA / "stations.json")
    all_stations = stations_obj["stations"]
    station_map = {int(s["id"]): s for s in all_stations}

    wanted_ids = used_station_ids()
    missing = sorted(wanted_ids - set(station_map))
    if missing:
        raise RuntimeError(
            f"{len(missing)} station IDs from municipalities.json are missing in stations.json"
        )

    used_stations = [station_map[sid] for sid in sorted(wanted_ids)]

    by_state = {}
    for station in used_stations:
        by_state.setdefault(station["state_slug"], []).append(int(station["id"]))

    local_now = now_vienna()
    today = local_now.date()
    start = today - timedelta(days=LOOKBACK_DAYS)

    newest = None
    updated_values = 0

    for state_slug, ids in sorted(by_state.items()):
        path = DATA / "current" / f"{state_slug}.json"

        if path.exists():
            obj = load_json(path)
        else:
            obj = {
                "metric": "tlmax",
                "unit": "°C",
                "year": today.year,
                "columns": ["date", "tlmax"],
                "stations": {},
            }

        # Important for 1 January: never mix two calendar years in one current file.
        if int(obj.get("year", today.year)) != today.year:
            obj = {
                "metric": "tlmax",
                "unit": "°C",
                "year": today.year,
                "columns": ["date", "tlmax"],
                "stations": {},
            }

        obj["year"] = today.year
        store = obj.setdefault("stations", {})

        rows = []
        for batch in chunks(ids, CHUNK_SIZE):
            rows.extend(parse_csv(fetch_chunk(batch, start, today)))

        for sid, day, value in rows:
            # The frontend current files contain only the current calendar year.
            if int(day[:4]) != today.year:
                continue

            series = store.setdefault(sid, [])
            mapping = {d: v for d, v in series}

            before = mapping.get(day)
            mapping[day] = value

            if before != value:
                updated_values += 1

            store[sid] = [[d, mapping[d]] for d in sorted(mapping)]

            if newest is None or day > newest:
                newest = day

        # Keep newest observation even when GeoSphere returned no changes today.
        for series in store.values():
            if series:
                candidate = series[-1][0]
                if newest is None or candidate > newest:
                    newest = candidate

        save_json(path, obj)

    if newest is None:
        newest = latest_existing_observation()

    save_json(
        DATA / "status.json",
        {
            "status": "ok",
            "last_update": local_now.isoformat(timespec="seconds"),
            "latest_observation": newest,
            "metric": "tlmax",
            "station_count": len(used_stations),
            "total_station_count": len(all_stations),
            "values_changed": updated_values,
            "source": "GeoSphere Austria",
        },
    )

    print(
        f"Updated {updated_values} values across {len(used_stations)} used stations; "
        f"latest observation: {newest}"
    )


def main():
    try:
        run_update()
    except Exception as exc:
        local_now = now_vienna()
        old_status = {}
        status_path = DATA / "status.json"

        if status_path.exists():
            try:
                old_status = load_json(status_path)
            except Exception:
                old_status = {}

        save_json(
            status_path,
            {
                "status": "error",
                "last_update": local_now.isoformat(timespec="seconds"),
                "latest_observation": old_status.get("latest_observation"),
                "metric": "tlmax",
                "station_count": old_status.get("station_count"),
                "total_station_count": old_status.get("total_station_count"),
                "values_changed": 0,
                "source": "GeoSphere Austria",
                "error": str(exc)[:500],
            },
        )

        print(f"Update failed: {exc}")
        raise


if __name__ == "__main__":
    main()
