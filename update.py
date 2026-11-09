"""Baja previsión, viento, sol y nieve modelada de Open-Meteo y escribe data/latest.json."""
import datetime
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
API = "https://api.open-meteo.com/v1/forecast"
BASE = {
    "daily": "temperature_2m_max,temperature_2m_min,snowfall_sum",
    "current": "temperature_2m",
}
EXTRA = {
    "daily": BASE["daily"] + ",weather_code,wind_speed_10m_max,wind_gusts_10m_max,sunshine_duration",
    "current": "temperature_2m,wind_speed_10m,wind_gusts_10m,cloud_cover",
}


def get(st, variables):
    q = {
        "latitude": st["lat"],
        "longitude": st["lon"],
        "elevation": round((st["alt_base"] + st["alt_top"]) / 2),
        "current": variables["current"],
        "daily": variables["daily"],
        "past_days": 7,
        "forecast_days": 5,
        "timezone": "Europe/Madrid",
    }
    url = API + "?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def parse(d):
    dl, cu = d["daily"], d["current"]
    n = len(dl["time"])

    def col(k):
        return dl.get(k) or [None] * n

    days = []
    for i in range(n):
        sun = col("sunshine_duration")[i]
        days.append({
            "date": dl["time"][i],
            "tmin": dl["temperature_2m_min"][i],
            "tmax": dl["temperature_2m_max"][i],
            "snow": dl["snowfall_sum"][i],
            "code": col("weather_code")[i],
            "wind": col("wind_speed_10m_max")[i],
            "gust": col("wind_gusts_10m_max")[i],
            "sun": None if sun is None else round(sun / 3600, 1),
        })
    return {
        "temp_now": cu["temperature_2m"],
        "wind_now": cu.get("wind_speed_10m"),
        "gust_now": cu.get("wind_gusts_10m"),
        "cloud_now": cu.get("cloud_cover"),
        "days": days,
    }


def fetch(st):
    for attempt in range(3):
        try:
            try:
                return parse(get(st, EXTRA))
            except urllib.error.HTTPError as e:
                if e.code != 400:
                    raise
                print(f"{st['id']}: variables extra no admitidas, uso las básicas")
                return parse(get(st, BASE))
        except Exception as e:  # noqa: BLE001
            print(f"{st['id']}: intento {attempt + 1} fallido: {e}")
            time.sleep(3)
    return {"temp_now": None, "days": []}


def main():
    stations = json.loads((ROOT / "stations.json").read_text(encoding="utf-8"))
    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "stations": [],
    }
    for st in stations:
        out["stations"].append({**st, **fetch(st)})
        time.sleep(0.3)
    if not any(s["days"] for s in out["stations"]):
        raise SystemExit("Ninguna estación devolvió datos; no se sobrescribe latest.json")
    dest = ROOT / "data" / "latest.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for s in out["stations"] if s["days"])
    print(f"OK: {ok}/{len(stations)} estaciones")


if __name__ == "__main__":
    main()
