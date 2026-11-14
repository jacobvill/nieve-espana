"""Genera los datos de la web:
- data/latest.json      resumen diario de cada estación (altitud media) + enlaces a webcams
- data/s/<id>.json      previsión hora a hora a tres alturas (base, media, cima) + temporada actual
- history/<id>.json     nieve acumulada por semana en temporadas anteriores (se completa poco a poco)
"""
import concurrent.futures
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
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
BASE = {
    "daily": "temperature_2m_max,temperature_2m_min,snowfall_sum",
    "current": "temperature_2m",
}
EXTRA = {
    "daily": BASE["daily"] + ",weather_code,wind_speed_10m_max,wind_gusts_10m_max,sunshine_duration",
    "current": "temperature_2m,wind_speed_10m,wind_gusts_10m,cloud_cover",
}
HOURLY = ("temperature_2m,apparent_temperature,snowfall,rain,weather_code,wind_speed_10m,wind_gusts_10m,"
          "wind_direction_10m,freezing_level_height,cloud_cover,sunshine_duration,snow_depth")
HOURLY_MIN = "temperature_2m,snowfall,weather_code,wind_speed_10m,wind_gusts_10m"
CAM_WORDS = ("webcam", "web-cam", "camara", "camera", "livecam", "live-cam", "kamera", "/cams")
HIST_PER_RUN = 20


def http_json(url, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < retries - 1:
                time.sleep(20 * (i + 1))
                continue
            raise


def mid_alt(st):
    return round((st["alt_base"] + st["alt_top"]) / 2)


def get(st, variables):
    q = {
        "latitude": st["lat"],
        "longitude": st["lon"],
        "elevation": mid_alt(st),
        "current": variables["current"],
        "daily": variables["daily"],
        "past_days": 7,
        "forecast_days": 7,
        "timezone": "Europe/Madrid",
    }
    return http_json(API + "?" + urllib.parse.urlencode(q))


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


# ---------- hora a hora a tres alturas ----------
def compact(d):
    h = d["hourly"]

    def r(k, nd=1, f=1.0):
        v = h.get(k)
        return None if v is None else [None if x is None else round(x * f, nd) for x in v]

    code = h.get("weather_code")
    return {
        "start": h["time"][0],
        "t": r("temperature_2m"), "at": r("apparent_temperature"), "sn": r("snowfall"), "rn": r("rain"),
        "code": None if code is None else [None if x is None else int(x) for x in code],
        "w": r("wind_speed_10m", 0), "g": r("wind_gusts_10m", 0), "wd": r("wind_direction_10m", 0),
        "fl": r("freezing_level_height", 0), "cl": r("cloud_cover", 0),
        "su": r("sunshine_duration", 1, 1 / 3600), "sd": r("snow_depth", 0, 100),
    }


def hourly(st, elev):
    for variables in (HOURLY, HOURLY_MIN):
        q = {"latitude": st["lat"], "longitude": st["lon"], "elevation": elev,
             "hourly": variables, "forecast_days": 7, "timezone": "Europe/Madrid"}
        try:
            return compact(http_json(API + "?" + urllib.parse.urlencode(q)))
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
    return None


def season_start(today):
    return datetime.date(today.year if today.month >= 10 else today.year - 1, 10, 1)


def archive(st, start, end):
    q = {"latitude": st["lat"], "longitude": st["lon"], "elevation": mid_alt(st),
         "start_date": start.isoformat(), "end_date": end.isoformat(),
         "daily": "snowfall_sum", "timezone": "Europe/Madrid"}
    d = http_json(ARCHIVE + "?" + urllib.parse.urlencode(q))
    return d["daily"]["time"], d["daily"]["snowfall_sum"]


def cur_season(st, today):
    start = season_start(today)
    end = today - datetime.timedelta(days=8)
    if end < start:
        return {"start": start.isoformat(), "daily": []}
    _, vals = archive(st, start, end)
    return {"start": start.isoformat(), "daily": [round(v or 0, 1) for v in vals]}


def fetch_detail(st, today):
    mid = mid_alt(st)
    alts = []
    for m in (st["alt_base"], mid, st["alt_top"]):
        h = hourly(st, m)
        if h is None:
            return None
        alts.append({"m": m, "h": h})
        time.sleep(0.1)
    det = {"start": alts[0]["h"]["start"], "alts": alts}
    for a in alts:
        a["h"].pop("start", None)
    try:
        det["season"] = cur_season(st, today)
    except Exception as e:  # noqa: BLE001
        print(f"{st['id']}: sin temporada actual ({e})")
    return det


def build_history(st, today):
    sy = season_start(today).year
    first = sy - 11
    times, vals = archive(st, datetime.date(first, 10, 1), datetime.date(sy, 4, 30))
    by = {}
    for t, v in zip(times, vals):
        d = datetime.date.fromisoformat(t)
        y = d.year if d.month >= 10 else d.year - 1
        by.setdefault(y, {})[(d - datetime.date(y, 10, 1)).days] = v or 0
    seasons = {}
    for y in range(first, sy):
        days = by.get(y, {})
        if len(days) < 180:
            continue
        seasons[str(y)] = [round(sum(days.get(k, 0) for k in range(0, 7 * i))) for i in range(31)]
    return {"seasons": seasons}


# ---------- webcams en la web oficial ----------
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


# ---------- proceso principal ----------
def process(st, overrides, today, t0):
    rec = {**st, **fetch(st)}
    cams = overrides.get(st["id"]) or []
    if not cams and st.get("web") and time.time() - t0 < 300:
        try:
            cams = find_webcams(st["web"])
        except Exception as e:  # noqa: BLE001
            print(f"{st['id']}: sin webcams ({e})")
    rec["webcams"] = cams
    det = None
    if rec["days"]:
        try:
            det = fetch_detail(st, today)
        except Exception as e:  # noqa: BLE001
            print(f"{st['id']}: sin detalle hora a hora ({e})")
    rec["detail"] = det is not None
    time.sleep(0.2)
    return rec, det


def main():
    today = datetime.date.today()
    stations = json.loads((ROOT / "stations.json").read_text(encoding="utf-8"))
    overrides = {}
    ov = ROOT / "webcams.json"
    if ov.exists():
        try:
            overrides = json.loads(ov.read_text(encoding="utf-8"))
        except ValueError:
            print("webcams.json no es un JSON válido; lo ignoro")

    hist = ROOT / "history"
    hist.mkdir(exist_ok=True)
    pending = [s for s in stations if not (hist / f"{s['id']}.json").exists()][:HIST_PER_RUN]
    for st in pending:
        try:
            (hist / f"{st['id']}.json").write_text(json.dumps(build_history(st, today), separators=(",", ":")), encoding="utf-8")
            print(f"histórico de {st['id']} listo")
        except Exception as e:  # noqa: BLE001
            print(f"{st['id']}: sin histórico ({e})")
        time.sleep(4)

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(lambda s: process(s, overrides, today, t0), stations))

    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "stations": [r for r, _ in results],
    }
    if not any(s["days"] for s in out["stations"]):
        raise SystemExit("Ninguna estación devolvió datos; no se sobrescribe latest.json")
    dd = ROOT / "data" / "s"
    dd.mkdir(parents=True, exist_ok=True)
    for r, det in results:
        if det is not None:
            (dd / f"{r['id']}.json").write_text(json.dumps(det, separators=(",", ":")), encoding="utf-8")
    (ROOT / "data" / "latest.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for s in out["stations"] if s["days"])
    print(f"OK: {ok}/{len(stations)} estaciones, {sum(1 for s in out['stations'] if s['detail'])} con hora a hora, "
          f"{sum(1 for s in out['stations'] if s['webcams'])} con webcams")


if __name__ == "__main__":
    main()
