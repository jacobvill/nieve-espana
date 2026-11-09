"""Baja previsión y nieve modelada de Open-Meteo y escribe docs/data/latest.json."""
import datetime
import json
import pathlib
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
API = "https://api.open-meteo.com/v1/forecast"


def fetch(st):
    q = {
        "latitude": st["lat"],
        "longitude": st["lon"],
        "elevation": round((st["alt_base"] + st["alt_top"]) / 2),
        "current": "temperature_2m",
        "daily": "temperature_2m_max,temperature_2m_min,snowfall_sum",
        "past_days": 7,
        "forecast_days": 5,
        "timezone": "Europe/Madrid",
    }
    url = API + "?" + urllib.parse.urlencode(q)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                d = json.load(r)
            dl = d["daily"]
            days = [
                {"date": t, "tmin": lo, "tmax": hi, "snow": sn}
                for t, lo, hi, sn in zip(
                    dl["time"],
                    dl["temperature_2m_min"],
                    dl["temperature_2m_max"],
                    dl["snowfall_sum"],
                )
            ]
            return {"temp_now": d["current"]["temperature_2m"], "days": days}
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
