// FIX F: was `export default function Alerts` — SatelliteScene now imports it
// as a default import (`import Alerts from './Alerts'`), which is consistent.
// Also added z-index and pointer-events:none so it overlays the Canvas without
// blocking OrbitControls mouse events.
export default function Alerts({ alerts }) {
  return (
    <div
      style={{
        position: "absolute",
        top: 10,
        right: 10,
        background: "rgba(10, 10, 20, 0.85)",
        backdropFilter: "blur(8px)",
        padding: "12px 16px",
        color: "white",
        width: "260px",
        borderRadius: "8px",
        border: "1px solid rgba(255,80,80,0.3)",
        zIndex: 100,
        // FIX G: pointer-events:none prevents the panel from eating mouse
        // events that should reach the OrbitControls canvas underneath.
        pointerEvents: "none",
        fontFamily: "monospace",
        fontSize: "12px",
      }}
    >
      <h3 style={{ margin: "0 0 8px", fontSize: "13px", color: "#ff6666" }}>
        ⚠️ Collision Alerts
      </h3>
      {alerts.length === 0 ? (
        <p style={{ color: "#88ff88" }}>No active threats</p>
      ) : (
        alerts.map((a, i) => (
          <div
            key={i}
            style={{
              color: "#ff4444",
              marginBottom: "4px",
              borderLeft: "2px solid #ff4444",
              paddingLeft: "6px",
            }}
          >
            {a.message}
          </div>
        ))
      )}
    </div>
  );
}