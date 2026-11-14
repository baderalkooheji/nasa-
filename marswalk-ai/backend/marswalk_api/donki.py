"""NASA DONKI space weather (solar flares + SEP events, last 7 days) for GET /api/spaceweather.

Live from https://api.nasa.gov/DONKI (NASA_API_KEY, default DEMO_KEY), refreshed at most
once an hour. The last good answer is saved to disk and used, marked "saved copy",
when the API cannot be reached. No answer at all -> status "unknown" (never guessed).

Caution rule (project-defined, same as the Scientific Intelligence Engine): any SEP event,
or any flare of class X or >= M5.0, in the last 7 days.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import httpx

API = "https://api.nasa.gov/DONKI"
CCMC = "https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get"
RULE = "Project-defined rule: caution if any SEP event or any flare of class X or ≥ M5.0 in the last 7 days"
REFRESH_S = 3600


def _strong(flare: dict) -> bool:
    c = str(flare.get("classType") or "")
    if c.startswith("X"):
        return True
    if c.startswith("M"):
        try:
            return float(c[1:]) >= 5.0
        except ValueError:
            return False
    return False


def classify(flares: list, seps: list) -> str:
    return "caution" if seps or any(_strong(f) for f in flares) else "nominal"


class Donki:
    def __init__(self, cache_file: Path, api_key: str = "DEMO_KEY", enabled: bool = True):
        self.cache_file = cache_file
        self.api_key = api_key
        self.enabled = enabled
        self._mem: dict | None = None

    def _saved(self) -> dict | None:
        if self._mem:
            return self._mem
        if self.cache_file.exists():
            try:
                return json.loads(self.cache_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    async def status(self, http: httpx.AsyncClient) -> dict:
        saved = self._saved()
        if saved and time.time() - saved["fetched_at"] < REFRESH_S:
            return self._answer(saved, cached=False)
        if self.enabled:
            end = dt.datetime.now(dt.timezone.utc).date()
            start = end - dt.timedelta(days=7)
            dates = {"startDate": start.isoformat(), "endDate": end.isoformat()}
            for base, extra in ((API, {"api_key": self.api_key}), (CCMC, {})):   # NASA API gateway, then CCMC's own service
                try:
                    fl = await http.get(f"{base}/FLR", params={**dates, **extra}, timeout=12)
                    sp = await http.get(f"{base}/SEP", params={**dates, **extra}, timeout=12)
                    fl.raise_for_status()
                    sp.raise_for_status()
                    data = {"fetched_at": time.time(), "window": [start.isoformat(), end.isoformat()],
                            "flares": fl.json() or [], "sep_events": sp.json() or [], "host": httpx.URL(base).host}
                    self._mem = data
                    self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                    self.cache_file.write_text(json.dumps(data), encoding="utf-8")
                    return self._answer(data, cached=False)
                except (httpx.HTTPError, ValueError):
                    continue
        if saved:
            return self._answer(saved, cached=True)
        return {"status": "unknown", "flares": [], "sep_events": [], "cached": False, "rule": RULE,
                "source": "NASA DONKI could not be reached and no saved copy exists"}

    @staticmethod
    def _answer(data: dict, cached: bool) -> dict:
        flares = [{"flrID": f.get("flrID"), "classType": f.get("classType"), "peakTime": f.get("peakTime"),
                   "sourceLocation": f.get("sourceLocation")} for f in data["flares"]]
        seps = [{"sepID": s.get("sepID"), "eventTime": s.get("eventTime")} for s in data["sep_events"]]
        when = dt.datetime.fromtimestamp(data["fetched_at"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return {"status": classify(data["flares"], data["sep_events"]), "flares": flares, "sep_events": seps,
                "cached": cached, "rule": RULE, "window": data.get("window"),
                "source": f"NASA DONKI ({data.get('host', 'api.nasa.gov')}), {'saved copy from' if cached else 'fetched'} {when}"}
