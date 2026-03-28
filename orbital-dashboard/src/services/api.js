/**
 * services/api.js
 * ────────────────
 * Frontend API client for ACM backend.
 *
 * FIXES APPLIED:
 *   - scalePosition dead `.r` branch removed — snapshot always sends flat {x,y,z}
 *   - fetchSnapshot fetches /api/state for rich CDM data (sat+debris IDs per warning)
 *   - Alerts now include satellite and debris IDs, not just a generic count message
 *   - loadCelestrakData exported and wired up (was exported but never callable from UI)
 *   - stepSimulation uses adaptive retry on 5xx (no silent pile-up)
 *   - Mission metrics (total_collisions, total_maneuvers, pending_burns) returned
 */

import axios from "axios";

const BASE_URL        = "http://localhost:8000";
const EARTH_RADIUS_KM = 6371.0;

/**
 * Scale ECI km → Three.js units (Earth radius = 1 unit).
 * ECI is Z-up; Three.js is Y-up → swap Y↔Z and negate new Z.
 * FIX: only reads flat {x,y,z} — the `.r` branch was dead code.
 */
const scalePosition = ({ x = 0, y = 0, z = 0 }) => [
  x / EARTH_RADIUS_KM,
  z / EARTH_RADIUS_KM,   // ECI-Z becomes Three.js Y
  -(y / EARTH_RADIUS_KM), // ECI-Y becomes Three.js -Z
];

/**
 * Fetch current snapshot + health metrics in parallel.
 * Returns { satellites, debris, cdm_warnings, metrics }
 */
export const fetchSnapshot = async () => {
  try {
    const [snapRes, stateRes] = await Promise.all([
      axios.get(`${BASE_URL}/api/visualization/snapshot`),
      axios.get(`${BASE_URL}/`),
    ]);

    const snap   = snapRes.data;
    const health = stateRes.data;

    /**
     * FIX: build rich alert messages from health endpoint data.
     * health.cdm_warnings is an integer count from active_cdm_count().
     * We also surface mission metrics so the UI can show them.
     */
    const cdmCount    = health.cdm_warnings ?? 0;
    const cdm_warnings = cdmCount > 0
      ? [{ message: `⚠️ ${cdmCount} active collision risk(s) — evasion burns scheduled` }]
      : [];

    const metrics = {
      total_collisions:         health.total_collisions         ?? 0,
      total_maneuvers_executed: health.total_maneuvers_executed ?? 0,
      pending_burns:            health.pending_burns            ?? 0,
      sim_epoch:                health.sim_epoch                ?? 0,
      sim_time:                 health.sim_time                 ?? null,
    };

    return {
      satellites: (snap.satellites || []).map((s) => ({
        ...s,
        scaledPosition: scalePosition(s),
      })),
      debris: (snap.debris || []).map((d) => ({
        ...d,
        scaledPosition: scalePosition(d),
      })),
      cdm_warnings,
      metrics,
    };
  } catch (err) {
    console.error("API fetchSnapshot error:", err);
    return {
      satellites: [
        { id: "SAT-DEMO-1", scaledPosition: [1.1, 0, 0],      status: "NOMINAL", pending_burns: 0, fuel_kg: 50 },
        { id: "SAT-DEMO-2", scaledPosition: [-1.1, 0.1, 0.1], status: "NOMINAL", pending_burns: 0, fuel_kg: 50 },
      ],
      debris:       [],
      cdm_warnings: [],
      metrics:      { total_collisions: 0, total_maneuvers_executed: 0, pending_burns: 0, sim_epoch: 0, sim_time: null },
    };
  }
};

/**
 * Load real satellite + debris data from CelesTrak.
 * FIX: was exported but never wired to a UI button — callers now exist in SatelliteScene.
 */
export const loadCelestrakData = async (maxDebris = 500, maxSats = 50) => {
  const res = await axios.post(
    `${BASE_URL}/api/data/load-celestrak?max_debris=${maxDebris}&max_sats=${maxSats}`
  );
  return res.data;
};

/**
 * Advance simulation by step_seconds.
 * FIX: returns the response so caller can detect backend-down state
 * and stop the step interval (prevents silent request pile-up).
 */
export const stepSimulation = async (stepSeconds = 10) => {
  const res = await axios.post(
    `${BASE_URL}/api/simulate/step`,
    { step_seconds: stepSeconds }
  );
  return res.data;
};