"""
Marswalk AI Assistant - Marswalk application client.

Thin async client for the REAL Marswalk backend (marswalk-ai/backend/marswalk_api, port 8085). Every number the
assistant reports about terrain, routes, EVA time or the mission log comes from these endpoints (NASA Mars 2020 DEMs),
never from the language model.

Endpoints used (all exist in marswalk_api/main.py):
    GET  /api/stops              habitat + planned stops (real Perseverance end-of-drive positions)
    GET  /api/elevation          DEM elevation + slope at one point
    GET  /api/terrain            DEM statistics within a radius (relief, slope mean / P90 / max, classification)
    POST /api/route/auto         least-cost route through stops (same planner as the "Auto-route" button)
    POST /api/route/options      up to 3 alternative routes between two points (balanced / gentlest / most direct)
    POST /api/route/analyze      metrics of an existing polyline (hand-drawn Route A)
    POST /api/eva                EVA time budget (modified Tobler) + reference radiation dose
    GET  /api/spaceweather       NASA DONKI flare / SEP / CME summary
    GET  /api/meda               MEDA weather (503 when no calibrated dataset is installed)
    GET/POST /api/records        mission log
    GET  /data/M20_waypoints.json  Perseverance localisations (NASA/JPL MMGIS), served as a static file
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from config import settings


class MarswalkUnavailable(Exception):
    """The Marswalk backend could not be reached or refused the request."""


class MarswalkClient:
    def __init__(self, base_url: str | None = None):
        self.base = (base_url or settings.MARSWALK_API_URL).rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, Any]] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=settings.TOOL_TIMEOUT)
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kw) -> Any:
        try:
            r = await self.client.request(method, self.base + path, **kw)
        except httpx.HTTPError as e:
            raise MarswalkUnavailable(f"Marswalk backend not reachable at {self.base} ({e.__class__.__name__}). "
                                      "Start it (port 8085) and try again.") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = r.text[:300]
            raise MarswalkUnavailable(f"{path} failed ({r.status_code}): {detail}")
        return r.json()

    async def _cached(self, key: str, ttl: float, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = await fn()
        self._cache[key] = (time.time(), val)
        return val

    # ------------------------------------------------------------------ endpoints
    async def health(self) -> dict:
        return await self._request("GET", "/api/health")

    async def stops(self) -> dict:
        return await self._cached("stops", 300, lambda: self._request("GET", "/api/stops"))

    async def elevation(self, lat: float, lon: float) -> dict:
        return await self._request("GET", "/api/elevation", params={"lat": lat, "lon": lon})

    async def terrain(self, lat: float, lon: float, radius: float = 150) -> dict:
        return await self._request("GET", "/api/terrain", params={"lat": lat, "lon": lon, "radius": radius})

    async def route_auto(self, stops: list[dict], max_slope: float = 15) -> dict:
        return await self._request("POST", "/api/route/auto", json={"stops": stops, "max_slope": max_slope})

    async def route_options(self, start: dict, end: dict, max_slope: float = 15, objectives: list[str] | None = None) -> dict:
        body = {"start": start, "end": end, "max_slope": max_slope}
        if objectives:
            body["objectives"] = objectives
        return await self._request("POST", "/api/route/options", json=body)

    async def route_analyze(self, waypoints: list[dict], max_slope: float | None = None) -> dict:
        body: dict = {"waypoints": waypoints}
        if max_slope:
            body["max_slope"] = max_slope
        return await self._request("POST", "/api/route/analyze", json=body)

    async def eva(self, out_waypoints: list[dict], back_waypoints: list[dict], n_stops: int = 0) -> dict:
        return await self._request("POST", "/api/eva", json={"out_waypoints": out_waypoints, "back_waypoints": back_waypoints,
                                                              "n_stops": n_stops})

    async def spaceweather(self) -> dict:
        return await self._cached("sw", 600, lambda: self._request("GET", "/api/spaceweather"))

    async def meda(self) -> dict:
        return await self._request("GET", "/api/meda")

    async def records(self, q: str = "") -> list:
        return await self._request("GET", "/api/records", params={"q": q})

    async def add_record(self, record: dict) -> dict:
        return await self._request("POST", "/api/records", json=record)

    async def rover_waypoints(self) -> dict:
        return await self._cached("m20", 3600, lambda: self._request("GET", "/data/M20_waypoints.json"))


marswalk = MarswalkClient()
