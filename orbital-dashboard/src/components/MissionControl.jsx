/**
 * MissionControl.jsx
 * ──────────────────
 * Collapsible mission control panel (right side of screen).
 */

import { useState, useEffect, useRef } from "react";

// ── Colour helpers ────────────────────────────────────────────────────────────
const RISK_COLOR = {
  CRITICAL: "#ff2200",
  HIGH:     "#ff8800",
  MEDIUM:   "#ffdd00",
  LOW:      "#00ffcc",
};

const STATUS_COLOR = {
  NOMINAL:     "#00ffee",
  MANEUVERING: "#ffee00",
  EOL:         "#ff8800",
  DEAD:        "#ff2200",
};

function badge(text, color) {
  return (
    <span style={{
      background: color + "22",
      color, border: `1px solid ${color}66`,
      borderRadius: 3, padding: "1px 5px",
      fontSize: 10, fontWeight: "bold", letterSpacing: 1,
    }}>
      {text}
    </span>
  );
}

// ── Countdown label ───────────────────────────────────────────────────────────
function Countdown({ seconds }) {
  if (seconds <= 0) return <span style={{ color: "#ff2200" }}>PAST TCA</span>;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const color = seconds < 120 ? "#ff2200" : seconds < 600 ? "#ff8800" : "#ffdd00";
  return <span style={{ color }}>{m}m {s}s</span>;
}

// ── Section wrapper ───────────────────────────────────────────────────────────
function Section({ title, count, color = "#00ccff", children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ marginBottom: 12 }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          cursor: "pointer", padding: "4px 0",
          borderBottom: "1px solid rgba(255,255,255,0.1)",
          marginBottom: open ? 6 : 0,
        }}
      >
        <span style={{ color, fontWeight: "bold", fontSize: 11 }}>
          {open ? "▾" : "▸"} {title}
        </span>
        {count != null && (
          <span style={{
            background: count > 0 ? color + "33" : "rgba(255,255,255,0.05)",
            color: count > 0 ? color : "#666",
            borderRadius: 10, padding: "1px 8px", fontSize: 10,
          }}>
            {count}
          </span>
        )}
      </div>
      {open && children}
    </div>
  );
}

// ── CDM Warning row ───────────────────────────────────────────────────────────
function CDMRow({ w }) {
  const tca = w.time_to_tca_s;
  const rc  = RISK_COLOR[w.risk_level] || "#ff8800";
  return (
    <div style={{
      background: "rgba(255,50,50,0.06)",
      border: `1px solid ${rc}44`,
      borderRadius: 4, padding: "6px 8px", marginBottom: 5,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
        <span style={{ color: "#fff", fontWeight: "bold", fontSize: 11 }}>
          {w.sat_id}
        </span>
        {badge(w.risk_level, rc)}
      </div>
      <div style={{ color: "#aaa", fontSize: 10, marginBottom: 2 }}>
        vs <span style={{ color: "#ff6666" }}>{w.deb_id}</span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10 }}>
        <span style={{ color: "#ccc" }}>
          Miss: <span style={{ color: rc }}>{(w.miss_distance_km * 1000).toFixed(0)} m</span>
        </span>
        <span style={{ color: "#ccc" }}>
          TCA: <Countdown seconds={tca} />
        </span>
      </div>
      <div style={{ marginTop: 3, fontSize: 10 }}>
        {w.evasion_pending
          ? <span style={{ color: "#ffee00" }}>🚀 Evasion burn scheduled</span>
          : <span style={{ color: "#ff4444" }}>⚠️ No evasion burn yet</span>
        }
        {w.sat_fuel_kg != null && (
          <span style={{ color: "#888", marginLeft: 8 }}>
            ⛽ {w.sat_fuel_kg} kg
          </span>
        )}
      </div>
    </div>
  );
}

// ── Burn row ──────────────────────────────────────────────────────────────────
function BurnRow({ b, simEpoch }) {
  const secsUntilBurn = b.burn_time_epoch - simEpoch;
  const isEva = b.burn_id.startsWith("EVA-");
  const isRec = b.burn_id.startsWith("REC-");
  const color = isEva ? "#ff8800" : isRec ? "#00ccff" : "#ffee00";
  const label = isEva ? "EVASION" : isRec ? "RECOVERY" : "MANEUVER";
  return (
    <div style={{
      background: "rgba(255,180,0,0.05)",
      border: "1px solid rgba(255,180,0,0.2)",
      borderRadius: 4, padding: "5px 8px", marginBottom: 4,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
        <span style={{ color: "#fff", fontSize: 11, fontWeight: "bold" }}>
          {b.satellite_id}
        </span>
        {badge(label, color)}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#aaa" }}>
        <span>ΔV: <span style={{ color: "#ffee00" }}>{b.delta_v_ms} m/s</span></span>
        <span>
          Fires in: {secsUntilBurn > 0
            ? <Countdown seconds={secsUntilBurn} />
            : <span style={{ color: "#ff8800" }}>next tick</span>}
        </span>
      </div>
    </div>
  );
}

// ── Satellite health row ──────────────────────────────────────────────────────
function SatRow({ s }) {
  const sc = STATUS_COLOR[s.status] || "#888";
  const fuelColor = s.fuel_pct < 10 ? "#ff2200" : s.fuel_pct < 25 ? "#ff8800" : "#00ffee";
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "3px 6px", marginBottom: 2,
      background: s.status === "DEAD" ? "rgba(255,0,0,0.08)" : "transparent",
      borderRadius: 3,
    }}>
      <span style={{ color: sc, fontSize: 10, flex: 1, overflow: "hidden",
        textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 130 }}>
        {s.status === "DEAD" ? "💀" : s.status === "EOL" ? "⚠️" : "●"} {s.id}
      </span>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <div style={{ width: 40, height: 5, background: "rgba(255,255,255,0.1)", borderRadius: 3 }}>
          <div style={{
            width: `${Math.max(0, s.fuel_pct)}%`, height: "100%",
            background: fuelColor, borderRadius: 3,
            transition: "width 0.5s",
          }} />
        </div>
        <span style={{ color: fuelColor, fontSize: 9, minWidth: 28, textAlign: "right" }}>
          {s.fuel_pct}%
        </span>
      </div>
    </div>
  );
}

// ── Collision flash overlay ───────────────────────────────────────────────────
function CollisionFlash({ collisions, onDismiss }) {
  if (collisions === 0) return null;
  return (
    <div
      onClick={onDismiss}
      style={{
        position: "fixed", inset: 0, zIndex: 999,
        display: "flex", alignItems: "center", justifyContent: "center",
        pointerEvents: "auto",
        animation: "collisionFlash 0.5s ease-out",
      }}
    >
      <style>{`
        @keyframes collisionFlash {
          0%   { background: rgba(255,0,0,0.5); }
          100% { background: rgba(255,0,0,0.0); }
        }
        @keyframes slideIn {
          from { transform: scale(0.7); opacity: 0; }
          to   { transform: scale(1);   opacity: 1; }
        }
      `}</style>
      <div style={{
        background: "rgba(10,5,5,0.95)",
        border: "2px solid #ff2200",
        borderRadius: 12, padding: "30px 40px",
        textAlign: "center", maxWidth: 360,
        boxShadow: "0 0 60px rgba(255,0,0,0.5)",
        animation: "slideIn 0.3s ease-out",
      }}>
        <div style={{ fontSize: 48, marginBottom: 12 }}>💥</div>
        <div style={{ color: "#ff2200", fontSize: 22, fontWeight: "bold",
          fontFamily: "monospace", marginBottom: 8 }}>
          COLLISION DETECTED
        </div>
        <div style={{ color: "#ff8888", fontSize: 14, marginBottom: 16 }}>
          {collisions} satellite{collisions > 1 ? "s" : ""} destroyed
        </div>
        <div style={{ color: "#888", fontSize: 11 }}>
          Satellite marked DEAD — debris field expanded
        </div>
        <div style={{ marginTop: 20, color: "#555", fontSize: 10 }}>
          Click anywhere to dismiss
        </div>
      </div>
    </div>
  );
}

// ── Main MissionControl component ─────────────────────────────────────────────
export default function MissionControl({ mission, simEpoch, prevCollisions, onCollisionSeen }) {
  const [collapsed, setCollapsed] = useState(false);
  const [showFlash,  setShowFlash] = useState(false);

  // FIX: prevColRef initialized to 0, not from the prevCollisions prop.
  // The prop is always prevCollisionsRef.current from SatelliteScene which is
  // 0 at mount and never triggers a re-render when updated (refs don't cause
  // re-renders). Initializing from the prop was therefore always 0 anyway but
  // misleading. Collision detection is self-contained via mission data.
  const prevColRef = useRef(0);

  // Detect new collisions → trigger flash
  useEffect(() => {
    if (mission && mission.total_collisions > prevColRef.current) {
      setShowFlash(true);
      prevColRef.current = mission.total_collisions;
      if (onCollisionSeen) onCollisionSeen(mission.total_collisions);
    }
  }, [mission?.total_collisions]);

  if (!mission) return null;

  const cdmCount  = mission.active_cdm_warnings?.length ?? 0;
  const burnCount = mission.scheduled_burns?.length ?? 0;
  const deadCount = mission.dead_satellites?.length ?? 0;

  return (
    <>
      {showFlash && (
        <CollisionFlash
          collisions={mission.total_collisions}
          onDismiss={() => setShowFlash(false)}
        />
      )}

      {/* Collapse toggle */}
      <button
        onClick={() => setCollapsed(c => !c)}
        style={{
          position: "absolute", top: 10, right: collapsed ? 10 : 310,
          zIndex: 200, background: "rgba(10,10,20,0.9)",
          border: "1px solid rgba(0,200,255,0.4)",
          color: "#00ccff", padding: "4px 10px",
          borderRadius: 5, fontFamily: "monospace", fontSize: 11,
          cursor: "pointer", backdropFilter: "blur(6px)",
          transition: "right 0.2s",
        }}
      >
        {collapsed ? "◀ Mission Control" : "▶ Hide"}
        {!collapsed && cdmCount > 0 && (
          <span style={{ marginLeft: 6, color: "#ff4444" }}>
            {cdmCount} ⚠️
          </span>
        )}
      </button>

      {!collapsed && (
        <div style={{
          position: "absolute", top: 44, right: 10,
          width: 300, maxHeight: "calc(100vh - 60px)",
          overflowY: "auto", overflowX: "hidden",
          background: "rgba(8,8,18,0.92)",
          backdropFilter: "blur(12px)",
          border: "1px solid rgba(0,200,255,0.2)",
          borderRadius: 8, padding: "12px",
          fontFamily: "monospace", fontSize: 11,
          zIndex: 100, pointerEvents: "auto",
          scrollbarWidth: "thin",
        }}>

          {/* Header */}
          <div style={{
            color: "#00ccff", fontWeight: "bold", fontSize: 13,
            marginBottom: 10, borderBottom: "1px solid rgba(0,200,255,0.2)",
            paddingBottom: 6, display: "flex", justifyContent: "space-between",
          }}>
            <span>🛰️ Mission Control</span>
            {deadCount > 0 && (
              <span style={{ color: "#ff2200", fontSize: 11 }}>
                💀 {deadCount} DEAD
              </span>
            )}
          </div>

          {/* ── CDM Warnings ── */}
          <Section
            title="⚠️ Conjunction Warnings"
            count={cdmCount}
            color={cdmCount > 0 ? "#ff4444" : "#666"}
            defaultOpen={true}
          >
            {cdmCount === 0 ? (
              <div style={{ color: "#44ff88", fontSize: 10, padding: "4px 0" }}>
                ✅ No active conjunction threats
              </div>
            ) : (
              // FIX: removed unused simEpoch prop from CDMRow — CDMRow uses
              // w.time_to_tca_s directly from backend, not a client-computed
              // epoch delta. Passing simEpoch was dead data causing confusion.
              mission.active_cdm_warnings.map((w, i) => (
                <CDMRow key={`${w.sat_id}-${w.deb_id}`} w={w} />
              ))
            )}
          </Section>

          {/* ── Scheduled Burns ── */}
          <Section
            title="🚀 Scheduled Burns"
            count={burnCount}
            color="#ffee00"
            defaultOpen={true}
          >
            {burnCount === 0 ? (
              <div style={{ color: "#888", fontSize: 10, padding: "4px 0" }}>
                No burns queued
              </div>
            ) : (
              mission.scheduled_burns.map((b, i) => (
                <BurnRow key={b.burn_id} b={b} simEpoch={simEpoch} />
              ))
            )}
          </Section>

          {/* ── Satellite Health ── */}
          <Section
            title="🛰️ Satellite Fleet"
            count={mission.satellite_health?.length ?? 0}
            color="#00ccff"
            defaultOpen={false}
          >
            {(mission.satellite_health || []).length === 0 ? (
              <div style={{ color: "#888", fontSize: 10 }}>No satellites tracked</div>
            ) : (
              <div>
                {[...mission.satellite_health]
                  .sort((a, b) => {
                    const order = { DEAD: 0, EOL: 1, MANEUVERING: 2, NOMINAL: 3 };
                    return (order[a.status] ?? 4) - (order[b.status] ?? 4);
                  })
                  .map((s) => <SatRow key={s.id} s={s} />)
                }
              </div>
            )}
          </Section>

          {/* ── Legend ── */}
          <div style={{
            borderTop: "1px solid rgba(255,255,255,0.08)",
            paddingTop: 8, marginTop: 4,
          }}>
            <div style={{ color: "#555", fontSize: 9, marginBottom: 4 }}>LEGEND</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 10px" }}>
              {[
                ["●", "#00ffee", "NOMINAL"],
                ["●", "#ffee00", "MANEUVERING"],
                ["●", "#ff8800", "EOL"],
                ["●", "#ff2200", "DEAD"],
              ].map(([sym, col, lbl]) => (
                <span key={lbl} style={{ fontSize: 9, color: "#888" }}>
                  <span style={{ color: col }}>{sym}</span> {lbl}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}