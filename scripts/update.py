from pathlib import Path
from datetime import timedelta, datetime, timezone
from zoneinfo import ZoneInfo
import csv
import io
import json
import math
import time

import requests

DAILY_BASE = "https://dataset.api.hub.geosphere.at/v1/station/historical/klima-v2-1d"
TENMIN_BASE = "https://dataset.api.hub.geosphere.at/v1/station/historical/klima-v2-10min"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"

# Re-read enough history so delayed official daily values can replace provisional values.
LOOKBACK_DAYS = 14
CHUNK_SIZE = 80
TIMEOUT = 90

# Guard against building a daily maximum from a severely incomplete 10-minute day.
MIN_10MIN_OBSERVATIONS = 108   # 75% of a normal 144-observation day
MIN_10MIN_SPAN_HOURS = 20


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

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


def fetch_chunk(base, parameter, ids, start, end):
    params = [
        ("parameters", parameter),
        ("start", start.isoformat()),
        ("end", end.isoformat()),
        ("output_format", "csv"),
    ]
    params += [("station_ids", str(x)) for x in ids]

    last_error = None
    for attempt in range(4):
        try:
            response = requests.get(base, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last_error = exc
            time.sleep(2 ** attempt)

    raise RuntimeError(
        f"GeoSphere request failed for {len(ids)} stations at {base}: {last_error}"
    )


def find_parameter_column(fieldnames, parameter):
    if not fieldnames:
        return None

    if parameter in fieldnames:
        return parameter

    prefix = parameter + " "
    for name in fieldnames:
        if name and (name.startswith(prefix) or name.startswith(parameter + "[")):
            return name

    return None


def parse_number(raw):
    if raw in (None, "", "nan", "NaN", "null", "None"):
        return None

    try:
        value = float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return None

    return value if math.isfinite(value) else None


def parse_time(raw):
    if not raw:
        return None

    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def now_vienna():
    return datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Vienna"))


# ---------------------------------------------------------------------------
# Station selection
# ---------------------------------------------------------------------------

def used_station_ids():
    """Return only stations that are actually used by the municipality search."""
    municipalities = load_json(DATA / "municipalities.json")
    columns = municipalities.get("columns", [])
    try:
        station_index = columns.index("station_id")
    except ValueError as exc:
        raise RuntimeError("municipalities.json has no station_id column") from exc

    return {
        int(row[station_index])
        for row in municipalities.get("municipalities", [])
        if row[station_index] not in (None, "")
    }


def latest_existing_observation():
    status_path = DATA / "status.json"
    if not status_path.exists():
        return None
    return load_json(status_path).get("latest_observation")


# ---------------------------------------------------------------------------
# Official daily tlmax parser
# ---------------------------------------------------------------------------

def parse_daily_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    parameter_column = find_parameter_column(reader.fieldnames, "tlmax")

    if parameter_column is None:
        raise RuntimeError(
            f"tlmax column not found in daily CSV. Columns: {reader.fieldnames}"
        )

    out = []
    for row in reader:
        sid = row.get("station") or row.get("station_id")
        raw_date = row.get("time") or row.get("date") or row.get("timestamp")
        value = parse_number(row.get(parameter_column))

        if not sid or not raw_date or value is None:
            continue

        try:
            day = str(raw_date)[:10]
            out.append((str(int(float(sid))), day, round(value, 1)))
        except (ValueError, TypeError):
            continue

    return out


# ---------------------------------------------------------------------------
# 10-minute fallback parser
# ---------------------------------------------------------------------------

def parse_10min_daily_maxima(text):
    """
    Build daily maxima from 10-minute air temperature (tl), grouped by
    Europe/Vienna calendar date.

    Only sufficiently complete days are returned. This data is used only when
    the official daily tlmax value is still missing. Once the official value
    arrives, the normal daily update overwrites the provisional value.
    """
    reader = csv.DictReader(io.StringIO(text))
    parameter_column = find_parameter_column(reader.fieldnames, "tl")

    if parameter_column is None:
        raise RuntimeError(
            f"tl column not found in 10-minute CSV. Columns: {reader.fieldnames}"
        )

    vienna = ZoneInfo("Europe/Vienna")
    grouped = {}

    for row in reader:
        sid = row.get("station") or row.get("station_id")
        raw_time = row.get("time") or row.get("date") or row.get("timestamp")
        dt = parse_time(raw_time)
        value = parse_number(row.get(parameter_column))

        if not sid or dt is None or value is None:
            continue

        try:
            sid = str(int(float(sid)))
        except (ValueError, TypeError):
            continue

        local_dt = dt.astimezone(vienna)
        day = local_dt.date().isoformat()
        grouped.setdefault((sid, day), []).append((local_dt, value))

    out = []
    for (sid, day), observations in grouped.items():
        observations.sort(key=lambda x: x[0])

        count = len(observations)
        span_hours = (
            observations[-1][0] - observations[0][0]
        ).total_seconds() / 3600

        if count < MIN_10MIN_OBSERVATIONS or span_hours < MIN_10MIN_SPAN_HOURS:
            continue

        daily_max = round(max(value for _, value in observations), 1)
        out.append((sid, day, daily_max, count))

    return out


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

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
    yesterday = today - timedelta(days=1)
    start = today - timedelta(days=LOOKBACK_DAYS)

    newest = None
    official_values_changed = 0
    provisional_values_added = 0
    provisional_station_days = []

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

        # 1) First load official quality-controlled daily maxima.
        daily_rows = []
        for batch in chunks(ids, CHUNK_SIZE):
            daily_rows.extend(
                parse_daily_csv(fetch_chunk(DAILY_BASE, "tlmax", batch, start, today))
            )

        for sid, day, value in daily_rows:
            if int(day[:4]) != today.year:
                continue

            series = store.setdefault(sid, [])
            mapping = {d: v for d, v in series}

            before = mapping.get(day)
            mapping[day] = value

            if before != value:
                official_values_changed += 1

            store[sid] = [[d, mapping[d]] for d in sorted(mapping)]

            if newest is None or day > newest:
                newest = day

        # 2) For completed days still missing after the daily update, fill from
        #    quality-controlled 10-minute temperature observations.
        #    Never use this fallback for today; today is represented by forecast.
        missing_days_by_station = {}
        for sid_int in ids:
            sid = str(sid_int)
            existing_days = {d for d, _ in store.get(sid, [])}
            missing_days = {
                (start + timedelta(days=offset)).isoformat()
                for offset in range((yesterday - start).days + 1)
                if (start + timedelta(days=offset)).year == today.year
                and (start + timedelta(days=offset)).isoformat() not in existing_days
            }
            if missing_days:
                missing_days_by_station[sid] = missing_days

        if missing_days_by_station and start <= yesterday:
            tenmin_rows = []
            for batch in chunks(ids, CHUNK_SIZE):
                tenmin_rows.extend(
                    parse_10min_daily_maxima(
                        fetch_chunk(TENMIN_BASE, "tl", batch, start, yesterday)
                    )
                )

            for sid, day, value, count in tenmin_rows:
                wanted_missing_days = missing_days_by_station.get(sid)
                if not wanted_missing_days or day not in wanted_missing_days:
                    continue

                series = store.setdefault(sid, [])
                mapping = {d: v for d, v in series}

                # Safety: never overwrite a value that appeared in the meantime.
                if day in mapping:
                    continue

                mapping[day] = value
                store[sid] = [[d, mapping[d]] for d in sorted(mapping)]
                provisional_values_added += 1
                provisional_station_days.append(
                    {"station_id": int(sid), "date": day, "observations": count}
                )

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
            "values_changed": official_values_changed + provisional_values_added,
            "official_values_changed": official_values_changed,
            "provisional_values_added": provisional_values_added,
            "provisional_source": "GeoSphere Austria klima-v2-10min",
            "source": "GeoSphere Austria",
        },
    )

    print(
        f"Official daily values changed: {official_values_changed}; "
        f"10-minute fallback values added: {provisional_values_added}; "
        f"latest observation: {newest}"
    )

    if provisional_station_days:
        print("Provisional station-days added from 10-minute observations:")
        for item in provisional_station_days[:50]:
            print(
                f"  station {item['station_id']} {item['date']} "
                f"({item['observations']} observations)"
            )
        if len(provisional_station_days) > 50:
            print(f"  ... and {len(provisional_station_days) - 50} more")


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
                "official_values_changed": 0,
                "provisional_values_added": 0,
                "source": "GeoSphere Austria",
                "error": str(exc)[:500],
            },
        )

        print(f"Update failed: {exc}")
        raise


if __name__ == "__main__":
    main()
