/**
 * services/api.js
 * ────────────────
 * Frontend API client for ACM backend.
 */

import axios from "axios";

const BASE_URL        = "http://localhost:8000";
const EARTH_RADIUS_KM = 6371.0;

/**
 * Scale ECI km → Three.js units (Earth radius = 1 unit).
 * ECI is Z-up; Three.js is Y-up → swap Y↔Z and negate new Z.
 */
const scalePosition = ({ x = 0, y = 0, z = 0 }) => [
  x / EARTH_RADIUS_KM,
  z / EARTH_RADIUS_KM,    // ECI-Z becomes Three.js Y
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

    const cdmCount     = health.cdm_warnings ?? 0;
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
 * FIX: fetchMission was imported and called by SatelliteScene.tsx but was
 * never defined or exported here, causing a hard runtime crash
 * ("fetchMission is not a function") on every 2s mission poll interval.
 * Fetches full CDM/burn/health detail from /api/mission.
 */
export const fetchMission = async () => {
  const res = await axios.get(`${BASE_URL}/api/mission`);
  return res.data;
};

/**
 * Load real satellite + debris data from CelesTrak.
 */
export const loadCelestrakData = async (maxDebris = 500, maxSats = 50) => {
  const res = await axios.post(
    `${BASE_URL}/api/data/load-celestrak?max_debris=${maxDebris}&max_sats=${maxSats}`
  );
  return res.data;
};

/**
 * Advance simulation by step_seconds.
 */
export const stepSimulation = async (stepSeconds = 10) => {
  const res = await axios.post(
    `${BASE_URL}/api/simulate/step`,
    { step_seconds: stepSeconds }
  );
  return res.data;
};

/**
 * Reset all simulation state on the backend.
 */
export const resetSimulation = async () => {
  const res = await axios.post(`${BASE_URL}/api/reset`);
  return res.data;
};