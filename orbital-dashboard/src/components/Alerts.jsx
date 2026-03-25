export default function Alerts({ alerts }) {
  return (
    <div style={{
      position: "absolute", top: 10, right: 10,
      background: "#111", padding: "10px",
      color: "white", width: "250px", borderRadius: "8px"
    }}>
      <h3>⚠️ Collision Alerts</h3>
      {alerts.length === 0
        ? <p>No threats</p>
        : alerts.map((a, i) => (
            <div key={i} style={{ color: "red" }}>{a.message}</div>
          ))
      }
    </div>
  );
}