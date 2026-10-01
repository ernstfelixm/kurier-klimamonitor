from pathlib import Path
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo
import csv, io, json, math, time
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
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i+n]

def fetch_chunk(ids, start, end):
    params = [("parameters", "tlmax"), ("start", start.isoformat()), ("end", end.isoformat()), ("output_format", "csv")]
    params += [("station_ids", str(x)) for x in ids]
    last = None
    for attempt in range(4):
        try:
            r = requests.get(BASE, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GeoSphere request failed for {len(ids)} stations: {last}")

def parse_csv(text):
    # GeoSphere daily CSV uses the same structure as the original download:
    # time,station,tlmax,substation (additional columns are safely ignored).
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

def main():
    stations_obj = load_json(DATA / "stations.json")
    stations = stations_obj["stations"]
    by_state = {}
    for s in stations:
        by_state.setdefault(s["state_slug"], []).append(int(s["id"]))

    today = date.today()
    start = today - timedelta(days=LOOKBACK_DAYS)
    newest = None
    updated_values = 0

    for state_slug, ids in sorted(by_state.items()):
        path = DATA / "current" / f"{state_slug}.json"
        obj = load_json(path) if path.exists() else {"metric":"tlmax","unit":"°C","year":today.year,"columns":["date","tlmax"],"stations":{}}
        store = obj.setdefault("stations", {})

        rows = []
        for batch in chunks(ids, CHUNK_SIZE):
            rows.extend(parse_csv(fetch_chunk(batch, start, today)))

        for sid, day, value in rows:
            if int(day[:4]) != today.year:
                continue
            series = store.setdefault(sid, [])
            mapping = {d: v for d, v in series}
            before = mapping.get(day)
            mapping[day] = value
            if before != value:
                updated_values += 1
            store[sid] = [[d, mapping[d]] for d in sorted(mapping)]
            newest = day if newest is None or day > newest else newest

        obj["year"] = today.year
        save_json(path, obj)

    vienna = ZoneInfo("Europe/Vienna")
    now = datetime.now(timezone.utc).astimezone(vienna).isoformat(timespec="seconds")
    old_status = load_json(DATA / "status.json") if (DATA / "status.json").exists() else {}
    if newest is None:
        newest = old_status.get("latest_observation")
    save_json(DATA / "status.json", {
        "status":"ok",
        "last_update":now,
        "latest_observation":newest,
        "metric":"tlmax",
        "station_count":len(stations),
        "values_changed":updated_values,
        "source":"GeoSphere Austria"
    })
    print(f"Updated {updated_values} values; latest observation: {newest}")

if __name__ == "__main__":
    main()
