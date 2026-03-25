import { create } from "zustand";

export const useStore = create((set) => ({
  satellites: [],
  debris: [],
  alerts: [],
  paths: [],

  setData: (data) => set({
    satellites: data.satellites || [],
    debris: data.debris || []
  }),

  generateAlerts: () => set((state) => {
    const alerts = [];
    state.satellites.forEach((sat) => {
      state.debris.forEach((d) => {
        const dist = Math.sqrt(
          (sat.x - d.x) ** 2 +
          (sat.y - d.y) ** 2 +
          (sat.z - d.z) ** 2
        );
        if (dist < 1.5) {
          alerts.push({ message: `⚠️ Collision risk near ${sat.id}` });
        }
      });
    });
    return { alerts };
  }),

  generatePaths: () => set((state) => {
    const paths = state.satellites.map((sat) => [
      [sat.x, sat.y, sat.z],
      [sat.x + 0.5, sat.y + 0.5, sat.z]
    ]);
    return { paths };
  })
}));

export default useStore;