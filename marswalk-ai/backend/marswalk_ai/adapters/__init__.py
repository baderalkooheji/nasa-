"""Integration adapters. Each adapter registers the capabilities it actually supports.

    standalone.py      local mission data, DEM terrain service, route service, document retrieval
    science_engine.py  Scientific Intelligence Engine (HTTP) - EVA, radiation, environment, space weather
    marswalk_http.py   the original Marswalk backend (/api/*) - registered only when configured and reachable
    frontend.py        map / navigation actions executed by the connected UI
"""
