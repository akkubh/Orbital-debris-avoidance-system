import { create } from "zustand";

export const useStore = create((set) => ({
  satellites: [],
  debris: [],
  alerts: [],
  paths: [],

  setData: (data) =>
    set({
      satellites: data.satellites || [],
      debris: data.debris || [],
    }),

  // FIX D: generateAlerts was comparing sat.x/d.x (raw km ECI) but after the
  // api.js transform every object carries scaledPosition (Three.js units).
  // Threshold updated to match: 0.1 km / 6371 ≈ 0.000016 units → use a
  // generous 0.05 (≈ 320 km) for a visible alert in the store-level check.
  // Real collision detection lives in the backend (conjunction.py); this is
  // just a lightweight client-side proximity indicator.
  generateAlerts: () =>
    set((state) => {
      const alerts = [];
      state.satellites.forEach((sat) => {
        const [sx, sy, sz] = sat.scaledPosition || [sat.x, sat.y, sat.z] || [];
        state.debris.forEach((d) => {
          const [dx, dy, dz] = d.scaledPosition || [d.x, d.y, d.z] || [];
          if (sx == null || dx == null) return;
          const dist = Math.sqrt(
            (sx - dx) ** 2 + (sy - dy) ** 2 + (sz - dz) ** 2
          );
          if (dist < 0.05) {
            alerts.push({ message: `⚠️ Proximity alert near ${sat.id}` });
          }
        });
      });
      return { alerts };
    }),

  // FIX E: generatePaths also used raw .x/.y/.z — updated to scaledPosition.
  generatePaths: () =>
    set((state) => {
      const paths = state.satellites
        .filter((s) => s.scaledPosition)
        .map((sat) => {
          const [x, y, z] = sat.scaledPosition;
          return [
            [x, y, z],
            [x + 0.05, y + 0.05, z],
          ];
        });
      return { paths };
    }),
}));

export default useStore;