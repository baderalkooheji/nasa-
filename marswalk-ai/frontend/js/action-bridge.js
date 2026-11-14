/* Marswalk AI - frontend action bridge.
 *
 * The assistant backend never touches the page. It returns typed frontend actions
 * (display_route, highlight_location, zoom_to_location, change_layer, clear_route,
 * navigate_to_section). A host page creates an ActionBridge with one handler per
 * action type it supports. The bridge:
 *   1. advertises what the page can do (capabilities, sections, layers) in the session handshake
 *   2. validates every incoming action against a strict schema (unknown types / bad payloads are refused)
 *   3. runs the host handler and reports the real outcome { ok, detail }
 * No arbitrary code is ever executed: only the registered handlers run.
 */
(function (global) {
  "use strict";

  const ACTION_TO_CAPABILITY = {
    display_route: "map.display_route",
    highlight_location: "map.highlight_location",
    zoom_to_location: "map.zoom_to_location",
    change_layer: "map.change_layer",
    clear_route: "map.clear_route",
    navigate_to_section: "navigation.open_section",
  };

  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const isLat = (v) => isNum(v) && v >= -90 && v <= 90;
  const isLon = (v) => isNum(v) && v >= -180 && v <= 180;
  const isStr = (v) => typeof v === "string" && v.length > 0 && v.length <= 200;

  /* Payload validators: return an error string or null. */
  const VALIDATORS = {
    display_route(p) {
      if (!isStr(p.route_id)) return "route_id missing";
      if (!Array.isArray(p.geometry) || p.geometry.length < 2) return "geometry needs at least 2 points";
      if (p.geometry.length > 20000) return "geometry too large";
      for (const pt of p.geometry) if (!Array.isArray(pt) || !isLat(pt[0]) || !isLon(pt[1])) return "invalid coordinate in geometry";
      if (p.slope_classes && !Array.isArray(p.slope_classes)) return "slope_classes must be an array";
      return null;
    },
    highlight_location(p) {
      if (!isLat(p.lat) || !isLon(p.lon)) return "lat/lon invalid";
      return null;
    },
    zoom_to_location(p) {
      if (p.bounds) {
        const b = p.bounds;
        if (!Array.isArray(b) || b.length !== 2 || !isLat(b[0][0]) || !isLon(b[0][1]) || !isLat(b[1][0]) || !isLon(b[1][1])) return "bounds invalid";
        return null;
      }
      if (!isLat(p.lat) || !isLon(p.lon)) return "lat/lon invalid";
      if (p.zoom != null && (!isNum(p.zoom) || p.zoom < 0 || p.zoom > 22)) return "zoom invalid";
      return null;
    },
    change_layer(p) {
      if (!isStr(p.layer_id)) return "layer_id missing";
      if (p.visible != null && typeof p.visible !== "boolean") return "visible must be boolean";
      if (p.opacity != null && (!isNum(p.opacity) || p.opacity < 0 || p.opacity > 1)) return "opacity must be 0..1";
      return null;
    },
    clear_route(p) {
      if (p.route_id != null && !isStr(p.route_id)) return "route_id invalid";
      return null;
    },
    navigate_to_section(p) {
      if (!isStr(p.section_id)) return "section_id missing";
      return null;
    },
  };

  class ActionBridge {
    /**
     * @param {object} opts
     * @param {string} opts.client                      name of the host page
     * @param {object} opts.handlers                    { action_type: async (payload) => string|void }  (throw to fail)
     * @param {() => Array} [opts.sections]             [{ id, label, keywords }]
     * @param {() => Array} [opts.layers]               [{ id, label, keywords }]
     * @param {() => object} [opts.getContext]          current UI state for the assistant
     */
    constructor(opts) {
      this.client = opts.client || "unknown";
      this.handlers = opts.handlers || {};
      this._sections = opts.sections || (() => []);
      this._layers = opts.layers || (() => []);
      this._getContext = opts.getContext || (() => ({}));
    }

    handshake() {
      const types = Object.keys(this.handlers).filter((t) => VALIDATORS[t]);
      return {
        client: this.client,
        capabilities: types.map((t) => ACTION_TO_CAPABILITY[t]),
        sections: this._sections(),
        layers: this._layers(),
      };
    }

    getContext() {
      try { return this._getContext() || {}; } catch (e) { return { context_error: String(e) }; }
    }

    /** Validate and execute one action. Always resolves to { ok, detail }. */
    async execute(action) {
      if (!action || typeof action !== "object") return { ok: false, detail: "malformed action" };
      const validate = VALIDATORS[action.type];
      const handler = this.handlers[action.type];
      if (!validate || !handler) return { ok: false, detail: `action type '${action.type}' is not supported by this page` };
      const payload = action.payload || {};
      const err = validate(payload);
      if (err) return { ok: false, detail: `rejected: ${err}` };
      try {
        const detail = await handler(payload, action);
        return { ok: true, detail: typeof detail === "string" ? detail : "done" };
      } catch (e) {
        return { ok: false, detail: String((e && e.message) || e) };
      }
    }
  }

  global.MarswalkActionBridge = ActionBridge;
  global.MarswalkActionTypes = Object.keys(ACTION_TO_CAPABILITY);
})(window);
