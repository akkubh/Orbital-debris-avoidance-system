import axios from "axios";

const API_URL = "http://localhost:8000/api/visualization/snapshot";
const EARTH_RADIUS_KM = 6371;

// Helper to scale coordinates (The "Math Bridge")
const scalePosition = (pos) => [
  (pos.x || 0) / EARTH_RADIUS_KM,
  (pos.z || 0) / EARTH_RADIUS_KM, // Swapping Y and Z for Three.js "Up" axis
  (pos.y || 0) / EARTH_RADIUS_KM
];

export const fetchSnapshot = async () => {
  try {
    const res = await axios.get(API_URL);
    const data = res.data;

    // Transform the raw API data into scaled 3D positions
    return {
      satellites: (data.satellites || []).map(s => ({
        ...s,
        scaledPosition: scalePosition(s)
      })),
      debris: (data.debris || []).map(d => ({
        ...d,
        scaledPosition: scalePosition(d)
      }))
    };
  } catch (err) {
    console.error("API Error", err);
    // Fallback data (also scaled so they show up near the Earth)
    return {
      satellites: [
        { id: "SAT1", scaledPosition: [1.1, 0, 0] },
        { id: "SAT2", scaledPosition: [-1.1, 0.1, 0.1] }
      ],
      debris: []
    };
  }
};

export const loadCelestrakData = async (maxDebris = 500, maxSats = 50) => {
  try {
    // Note: Added full URL since your backend is on port 8000
    const res = await axios.post(
      `http://localhost:8000/api/data/load-celestrak?max_debris=${maxDebris}&max_sats=${maxSats}`
    );
    return res.data;
  } catch (err) {
    console.error("CelesTrak load error", err);
  }
};