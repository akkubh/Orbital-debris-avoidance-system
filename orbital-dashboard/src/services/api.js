import axios from "axios";

const BASE = "http://localhost:8000";

const SCALE = 1 / 6000; // ECI km → Three.js units

function scalePosition(x, y, z) {
  return [x * SCALE, y * SCALE, z * SCALE];
}

export const fetchSnapshot = async () => {
  const [snap, health] = await Promise.all([
    axios.get(`${BASE}/api/visualization/snapshot`),
    axios.get(`${BASE}/`)
  ]);

  const satellites = (snap.data.satellites || []).map(s => ({
    ...s,
    scaledPosition: scalePosition(s.x, s.y, s.z)
  }));

  const debris = (snap.data.debris || []).map(d => ({
    ...d,
    scaledPosition: scalePosition(d.x, d.y, d.z)
  }));

  return {
    satellites,
    debris,
    metrics: health.data
  };
};

export const fetchMission = async () => {
  const res = await axios.get(`${BASE}/api/mission`);
  return res.data;
};

export const stepSimulation = async (seconds = 10) => {
  const res = await axios.post(`${BASE}/api/simulate/step`, {
    step_seconds: seconds
  });
  return res.data;
};

export const loadCelestrakData = async (maxDebris = 500, maxSats = 50) => {
  const res = await axios.post(
    `${BASE}/api/data/load-celestrak?max_debris=${maxDebris}&max_sats=${maxSats}`
  );
  return res.data;
};

export const resetSimulation = async () => {
  const res = await axios.post(`${BASE}/api/reset`);
  return res.data;
};

export const scheduleQuickBurn = async (satId, direction, dvMs, delayS) => {
  const res = await axios.post(`${BASE}/api/maneuver/quick`, {
    satellite_id: satId,
    direction,
    dv_ms: dvMs,
    delay_s: delayS
  });
  return res.data;
};