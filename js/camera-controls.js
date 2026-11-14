/**
 * camera-controls.js — camera transitions and navigation helpers.
 */

import { MARS_WKID } from "./map-layers.js";
import { JEZERO, jezeroCamera } from "./jezero-region.js";

export const GLOBAL_ALTITUDE = 9_000_000; // m above the surface; whole disk in view

export function initialCamera() {
  return {
    position: { x: JEZERO.center.lon - 25, y: 12, z: GLOBAL_ALTITUDE, spatialReference: { wkid: MARS_WKID } },
    heading: 0,
    tilt: 0,
  };
}

export function createCameraControls(esri, view) {
  let spinTask = null;
  let flight = 0; // id of the latest flight so a newer request cancels older steps

  function stopSpin() {
    spinTask?.remove();
    spinTask = null;
  }

  /** Slow rotation of the globe until the user interacts (as in Explore Mars). */
  function startSpin() {
    stopSpin();
    spinTask = esri.scheduling.addFrameTask({
      update: () => {
        if (view.interacting) return stopSpin();
        const cam = view.camera?.clone();
        if (!cam || cam.position.longitude == null || cam.position.z < 2_000_000) return;
        cam.position.longitude += 0.012;
        view.camera = cam;
      },
    });
  }

  const ignoreAbort = (err) => {
    if (err?.name !== "AbortError") console.warn("[Marswalk3D] camera:", err);
  };

  /** Rotate to Jezero at orbital altitude, then descend into a tilted crater view. */
  async function goToJezero({ duration = 6500 } = {}) {
    stopSpin();
    const id = ++flight;
    const alt = view.camera.position.z;
    try {
      if (alt > 600_000) {
        await view.goTo(
          {
            position: { x: JEZERO.center.lon, y: JEZERO.center.lat - 4, z: Math.max(3_000_000, Math.min(alt, GLOBAL_ALTITUDE)), spatialReference: { wkid: MARS_WKID } },
            heading: 0,
            tilt: 0,
          },
          { duration: duration * 0.4, easing: "in-out-cubic" },
        );
      }
      if (id !== flight) return;
      await view.goTo(jezeroCamera(), { duration: duration * 0.6, easing: "in-out-cubic" });
    } catch (err) {
      ignoreAbort(err);
    }
  }

  /** Pull back out to the full globe, keeping the current longitude in view. */
  async function goToGlobal({ duration = 4000 } = {}) {
    stopSpin();
    ++flight;
    const lon = view.camera?.position?.longitude ?? initialCamera().position.x;
    try {
      await view.goTo(
        { position: { x: lon, y: 12, z: GLOBAL_ALTITUDE, spatialReference: { wkid: MARS_WKID } }, heading: 0, tilt: 0 },
        { duration, easing: "in-out-cubic" },
      );
    } catch (err) {
      ignoreAbort(err);
    }
  }

  /**
   * Fly to a latitude / longitude.
   * @param {number} lat
   * @param {number} lon
   * @param {{altitude?: number, tilt?: number, heading?: number, duration?: number}} [opts]
   */
  async function goToLocation(lat, lon, opts = {}) {
    stopSpin();
    ++flight;
    const { altitude = 25_000, tilt = 50, heading = 0, duration = 4000 } = opts;
    // Camera placed "behind" the target so that it looks at it from `altitude` with `tilt`.
    const tiltRad = (tilt * Math.PI) / 180;
    const back = altitude * Math.tan(tiltRad) / 59274.7; // degrees of latitude behind the target
    const headRad = (heading * Math.PI) / 180;
    const camLat = lat - back * Math.cos(headRad);
    const camLon = lon - (back * Math.sin(headRad)) / Math.cos((lat * Math.PI) / 180);
    try {
      await view.goTo(
        { position: { x: camLon, y: camLat, z: altitude, spatialReference: { wkid: MARS_WKID } }, heading, tilt },
        { duration, easing: "in-out-cubic" },
      );
    } catch (err) {
      ignoreAbort(err);
    }
  }

  function zoom(factor) {
    stopSpin();
    ++flight;
    // Keeps heading and tilt; moves towards / away from the point at the centre of the screen.
    view.goTo({ target: view.center, scale: view.scale * factor }, { duration: 600 }).catch(ignoreAbort);
  }

  function reset() {
    ++flight;
    view.goTo(initialCamera(), { duration: 3000, easing: "in-out-cubic" }).catch(ignoreAbort);
  }

  return {
    goToJezero,
    goToGlobal,
    goToLocation,
    zoomIn: () => zoom(0.5),
    zoomOut: () => zoom(2),
    reset,
    startSpin,
    stopSpin,
  };
}
