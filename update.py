"""Baja previsión, viento, sol y nieve modelada de Open-Meteo, busca webcams en la web
oficial de cada estación y escribe data/latest.json."""
import datetime
import html.parser
import json
import pathlib
import time
import unicodedata
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
CAM_WORDS = ("webcam", "web-cam", "camara", "camera", "livecam", "live-cam", "kamera", "/cams")


def get(st, variables):
    q = {
        "latitude": st["lat"],
        "longitude": st["lon"],
        "elevation": round((st["alt_base"] + st["alt_top"]) / 2),
        "current": variables["current"],
        "daily": variables["daily"],
        "past_days": 7,
        "forecast_days": 7,
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


class Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a":
            if self._href:
                self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def norm(s):
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()


def webcams_from_html(page, base):
    p = Links()
    p.feed(page)
    found, seen = [], set()
    for href, text in p.links:
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        key = norm(href + " " + text)
        url = urllib.parse.urljoin(base, href).split("#")[0]
        if url in seen or not any(w in key for w in CAM_WORDS):
            continue
        seen.add(url)
        found.append((0 if "webcam" in key else 1, {"title": (text[:40] or "Webcams"), "url": url}))
    found.sort(key=lambda x: x[0])
    return [c for _, c in found[:3]]


def find_webcams(web):
    req = urllib.request.Request(web, headers={"User-Agent": "Mozilla/5.0 (compatible; nieve-en-europa/1.0)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        page = r.read(600_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
        return webcams_from_html(page, r.geturl())


def main():
    stations = json.loads((ROOT / "stations.json").read_text(encoding="utf-8"))
    overrides = {}
    ov = ROOT / "webcams.json"
    if ov.exists():
        try:
            overrides = json.loads(ov.read_text(encoding="utf-8"))
        except ValueError:
            print("webcams.json no es un JSON válido; lo ignoro")
    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "stations": [],
    }
    t0 = time.time()
    for st in stations:
        cams = overrides.get(st["id"]) or []
        if not cams and st.get("web") and time.time() - t0 < 240:
            try:
                cams = find_webcams(st["web"])
            except Exception as e:  # noqa: BLE001
                print(f"{st['id']}: sin webcams ({e})")
        out["stations"].append({**st, **fetch(st), "webcams": cams})
        time.sleep(0.3)
    if not any(s["days"] for s in out["stations"]):
        raise SystemExit("Ninguna estación devolvió datos; no se sobrescribe latest.json")
    dest = ROOT / "data" / "latest.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for s in out["stations"] if s["days"])
    cams = sum(1 for s in out["stations"] if s["webcams"])
    print(f"OK: {ok}/{len(stations)} estaciones, {cams} con enlace a webcams")


if __name__ == "__main__":
    main()
