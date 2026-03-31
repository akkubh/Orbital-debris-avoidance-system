/**
 * SatelliteScene.tsx — Main 3-D scene + Mission Control integration
 */
import React, { useState, useEffect, useRef, Suspense } from "react";
import * as THREE from "three";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls, Stars, useTexture, PerspectiveCamera } from "@react-three/drei";
import { OrbitalShell } from "./OrbitalShell";
import MissionControl from "./MissionControl";
import {
  fetchSnapshot, fetchMission,
  stepSimulation, loadCelestrakData, resetSimulation,
} from "../services/api";

// ── Earth ─────────────────────────────────────────────────────────────────────
function Earth() {
  const texture = useTexture(
    "https://raw.githubusercontent.com/mrdoob/three.js/master/examples/textures/planets/earth_atmos_2048.jpg"
  );
  return (
    <mesh rotation={[0, Math.PI / 2, 0]}>
      <sphereGeometry args={[1, 64, 64]} />
      <meshStandardMaterial map={texture} roughness={0.7} metalness={0.2} />
    </mesh>
  );
}

// ── DebrisShell ───────────────────────────────────────────────────────────────
interface ShellData {
  id: string;
  scaledPosition: [number, number, number];
  status?: string;
  pending_burns?: number;
}

const MAX_DEBRIS = 5000;

// FIX: circleTex created once at module level, not inside the component render
// body. Creating it inside the component (even with a ref guard) is a side
// effect in render which React StrictMode double-invokes, leaking a canvas
// texture on every dev mount. Module-level creation runs exactly once.
function makeCircleTex(): THREE.Texture {
  const canvas      = document.createElement("canvas");
  canvas.width      = canvas.height = 32;
  const ctx         = canvas.getContext("2d")!;
  const g           = ctx.createRadialGradient(16, 16, 0, 16, 16, 16);
  g.addColorStop(0,   "rgba(255,255,255,1)");
  g.addColorStop(0.6, "rgba(255,255,255,0.6)");
  g.addColorStop(1,   "rgba(255,255,255,0)");
  ctx.fillStyle     = g;
  ctx.fillRect(0, 0, 32, 32);
  return new THREE.CanvasTexture(canvas);
}
const DEBRIS_CIRCLE_TEX = makeCircleTex();

function DebrisShell({ data }: { data: ShellData[] }) {
  const dataRef   = useRef<ShellData[]>(data);
  dataRef.current = data;

  const pointsRef = useRef<THREE.Points>(null!);
  const posAttr   = useRef<THREE.BufferAttribute | null>(null);

  useEffect(() => {
    const geo   = new THREE.BufferGeometry();
    const pos   = new Float32Array(MAX_DEBRIS * 3);
    posAttr.current = new THREE.BufferAttribute(pos, 3);
    posAttr.current.setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute("position", posAttr.current);
    geo.setDrawRange(0, 0);
    if (pointsRef.current) {
      pointsRef.current.geometry.dispose();
      pointsRef.current.geometry = geo;
    }
  }, []);

  useFrame(() => {
    const d = dataRef.current;
    if (!pointsRef.current || !posAttr.current) return;
    if (!d?.length) { pointsRef.current.geometry.setDrawRange(0, 0); return; }
    const count  = Math.min(d.length, MAX_DEBRIS);
    const posArr = posAttr.current.array as Float32Array;
    for (let i = 0; i < count; i++) {
      const [x, y, z]   = d[i].scaledPosition;
      posArr[i * 3]     = x;
      posArr[i * 3 + 1] = y;
      posArr[i * 3 + 2] = z;
    }
    posAttr.current.needsUpdate = true;
    pointsRef.current.geometry.setDrawRange(0, count);
  });

  return (
    <points ref={pointsRef}>
      <bufferGeometry />
      <pointsMaterial
        color="#ff5555"
        map={DEBRIS_CIRCLE_TEX}
        alphaMap={DEBRIS_CIRCLE_TEX}
        alphaTest={0.01}
        sizeAttenuation={false}
        size={4}
        depthWrite={false}
        transparent
      />
    </points>
  );
}

// ── Metrics HUD (bottom-left) ─────────────────────────────────────────────────
function MetricsHUD({ metrics, offline }: { metrics: any; offline: boolean }) {
  const t = metrics.sim_time
    ? new Date(metrics.sim_time).toUTCString().slice(5, 25)
    : "—";
  return (
    <div style={{
      position: "absolute", bottom: 10, left: 10,
      background: "rgba(10,10,20,0.88)", backdropFilter: "blur(8px)",
      padding: "10px 14px", color: "white", borderRadius: 8,
      border: "1px solid rgba(0,200,255,0.2)", fontFamily: "monospace",
      fontSize: 11, zIndex: 100, pointerEvents: "none", minWidth: 220,
    }}>
      {offline && <div style={{ color: "#ff8888", marginBottom: 5 }}>⚠️ Backend offline</div>}
      <div style={{ color: "#00ccff", marginBottom: 6, fontSize: 12, fontWeight: "bold" }}>
        📡 ACM Status
      </div>
      <div>🕐 {t}</div>
      <div>⏱  Epoch: {(metrics.sim_epoch ?? 0).toFixed(0)}s</div>
      <div>💥 Collisions: {metrics.total_collisions ?? 0}</div>
      <div>🚀 Maneuvers: {metrics.total_maneuvers_executed ?? 0}</div>
      <div>⏳ Pending burns: {metrics.pending_burns ?? 0}</div>
    </div>
  );
}

// ── SatelliteScene ────────────────────────────────────────────────────────────
export default function SatelliteScene() {
  const [satData,    setSatData]    = useState<ShellData[]>([]);
  const [debrisData, setDebrisData] = useState<ShellData[]>([]);
  const [metrics,    setMetrics]    = useState<any>({
    total_collisions: 0, total_maneuvers_executed: 0,
    pending_burns: 0, sim_epoch: 0, sim_time: null,
  });
  const [mission,    setMission]    = useState<any>(null);
  const [offline,    setOffline]    = useState(false);
  const [celestrakLoading, setCelestrakLoading] = useState(false);
  const [resetting,        setResetting]        = useState(false);

  const stepFailRef       = useRef(0);
  const prevCollisionsRef = useRef(0);
  const MAX_FAILS         = 3;

  useEffect(() => {
    // FIX: isMounted guard prevents setState calls on unmounted component.
    // Without this, the immediate fetchSnapshot/fetchMission calls at the
    // bottom of the effect resolve after unmount (e.g. in StrictMode double
    // invoke) and trigger "Can't perform a React state update on an unmounted
    // component" warnings and potential memory leaks.
    let isMounted = true;

    // ── Snapshot poll: positions + metrics every 1s ───────────────────────
    const snapPoll = setInterval(async () => {
      try {
        const result = await fetchSnapshot();
        if (!isMounted) return;
        setSatData(result.satellites  || []);
        setDebrisData(result.debris   || []);
        setMetrics(result.metrics);
        setOffline(false);
        stepFailRef.current = 0;
      } catch {
        if (isMounted) setOffline(true);
      }
    }, 1000);

    // ── Mission poll: CDM / burns / health every 2s ───────────────────────
    const missionPoll = setInterval(async () => {
      try {
        const m = await fetchMission();
        if (isMounted) setMission(m);
      } catch {}
    }, 2000);

    // ── Sim step every 5s (skip when tab hidden) ──────────────────────────
    const stepPoll = setInterval(async () => {
      if (document.hidden) return;
      if (stepFailRef.current >= MAX_FAILS) return;
      try {
        await stepSimulation(10);
        stepFailRef.current = 0;
      } catch {
        stepFailRef.current += 1;
        if (stepFailRef.current >= MAX_FAILS && isMounted) setOffline(true);
      }
    }, 5000);

    // Trigger first fetches immediately
    fetchSnapshot().then(r => {
      if (!isMounted) return;
      setSatData(r.satellites || []);
      setDebrisData(r.debris  || []);
      setMetrics(r.metrics);
    }).catch(() => { if (isMounted) setOffline(true); });

    fetchMission().then(m => {
      if (isMounted) setMission(m);
    }).catch(() => {});

    return () => {
      isMounted = false;
      clearInterval(snapPoll);
      clearInterval(missionPoll);
      clearInterval(stepPoll);
    };
  }, []);

  const handleLoadCelestrak = async () => {
    setCelestrakLoading(true);
    try {
      await loadCelestrakData(500, 50);
    } catch (e) {
      console.error("CelesTrak load failed:", e);
    } finally {
      setCelestrakLoading(false);
    }
  };

  const handleReset = async () => {
    setSatData([]);
    setDebrisData([]);
    setMission(null);
    setMetrics({ total_collisions: 0, total_maneuvers_executed: 0,
                 pending_burns: 0, sim_epoch: 0, sim_time: null });
    setOffline(false);
    stepFailRef.current = 0;
    prevCollisionsRef.current = 0;
    setResetting(true);
    try { await resetSimulation(); } catch (e) { console.error("Reset:", e); }
    finally { setResetting(false); }
  };

  const btnBase: React.CSSProperties = {
    position: "absolute", top: 10, zIndex: 150,
    background: "rgba(10,10,20,0.9)", backdropFilter: "blur(6px)",
    padding: "6px 14px", borderRadius: 6,
    fontFamily: "monospace", fontSize: 12, cursor: "pointer",
  };

  return (
    <div style={{ width: "100vw", height: "100vh", background: "#000", position: "relative" }}>
      <MetricsHUD metrics={metrics} offline={offline} />

      <MissionControl
        mission={mission}
        simEpoch={metrics.sim_epoch ?? 0}
        prevCollisions={prevCollisionsRef.current}
        onCollisionSeen={(n: number) => { prevCollisionsRef.current = n; }}
      />

      {/* Buttons */}
      <button
        onClick={handleLoadCelestrak}
        disabled={celestrakLoading}
        style={{ ...btnBase, left: 10, border: "1px solid rgba(0,200,255,0.4)", color: "#00ccff" }}
      >
        {celestrakLoading ? "Loading…" : "🛰️ Load CelesTrak"}
      </button>
      <button
        onClick={handleReset}
        disabled={resetting}
        style={{ ...btnBase, left: 170, border: "1px solid rgba(255,80,80,0.4)", color: "#ff6666" }}
      >
        {resetting ? "Resetting…" : "🗑️ Reset State"}
      </button>

      <Canvas flat>
        <color attach="background" args={["#010101"]} />
        <PerspectiveCamera makeDefault position={[0, 0, 5]} />
        <ambientLight intensity={0.8} />
        <pointLight position={[10, 10, 10]} intensity={2.0} />
        <directionalLight position={[-5, 3, 5]} intensity={1.5} />

        <Suspense fallback={
          <mesh>
            <sphereGeometry args={[1, 32, 32]} />
            <meshBasicMaterial color="#112244" wireframe />
          </mesh>
        }>
          <Earth />
        </Suspense>

        <OrbitalShell data={satData} />
        <DebrisShell  data={debrisData} />

        <Stars radius={300} count={7000} factor={7} saturation={0} fade speed={1} />
        <OrbitControls enablePan={false} minDistance={1.5} maxDistance={20} />
      </Canvas>
    </div>
  );
}