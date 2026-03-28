/**
 * components/SatelliteScene.tsx
 * ──────────────────────────────
 * Main 3-D scene: Earth, satellites, debris, alerts, HUD.
 *
 * FIXES APPLIED:
 *   - Import path corrected to '../services/api' (was broken)
 *   - Backend-offline detection: step interval pauses on repeated failures
 *   - DebrisShell instanceColor buffer initialized on mount (prevents wrong
 *     colors on some GPU drivers)
 *   - loadCelestrakData wired to a UI button
 *   - Mission metrics HUD (collisions, maneuvers, pending burns, sim time)
 *   - stepSimulation failure stops the interval (no silent pile-up)
 *   - Demo fallback shown with offline banner, not silently
 */

import React, { useState, useEffect, useRef, Suspense } from "react";
import * as THREE from "three";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls, Stars, useTexture, PerspectiveCamera } from "@react-three/drei";
import { OrbitalShell } from "./OrbitalShell";
import { fetchSnapshot, stepSimulation, loadCelestrakData } from "../services/api";
import Alerts from "./Alerts";

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

const MAX_DEBRIS_INSTANCES = 5000;

function DebrisShell({ data }: { data: ShellData[] }) {
  const meshRef = useRef<THREE.InstancedMesh>(null!);
  const tmp     = useRef(new THREE.Object3D());

  // FIX: initialize instanceColor buffer on mount to prevent wrong colors
  useEffect(() => {
    if (!meshRef.current) return;
    meshRef.current.setColorAt(0, new THREE.Color("#ff4444"));
    if (meshRef.current.instanceColor) {
      meshRef.current.instanceColor.needsUpdate = true;
    }
  }, []);

  useFrame(() => {
    if (!meshRef.current || !data?.length) return;
    const count = Math.min(data.length, MAX_DEBRIS_INSTANCES);
    for (let i = 0; i < count; i++) {
      const [x, y, z] = data[i].scaledPosition;
      tmp.current.position.set(x, y, z);
      tmp.current.updateMatrix();
      meshRef.current.setMatrixAt(i, tmp.current.matrix);
    }
    meshRef.current.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, MAX_DEBRIS_INSTANCES]}>
      <sphereGeometry args={[0.004, 6, 6]} />
      <meshBasicMaterial color="#ff4444" transparent opacity={0.7} />
    </instancedMesh>
  );
}

// ── Metrics HUD ───────────────────────────────────────────────────────────────
interface Metrics {
  total_collisions: number;
  total_maneuvers_executed: number;
  pending_burns: number;
  sim_epoch: number;
  sim_time: string | null;
}

function MetricsHUD({ metrics, offline }: { metrics: Metrics; offline: boolean }) {
  return (
    <div style={{
      position: "absolute", bottom: 10, left: 10,
      background: "rgba(10,10,20,0.85)", backdropFilter: "blur(8px)",
      padding: "10px 14px", color: "white", borderRadius: 8,
      border: "1px solid rgba(0,200,255,0.3)", fontFamily: "monospace",
      fontSize: 11, zIndex: 100, pointerEvents: "none", minWidth: 220,
    }}>
      {offline && (
        <div style={{ color: "#ff8888", marginBottom: 6 }}>⚠️ Backend offline</div>
      )}
      <div style={{ color: "#00ccff", marginBottom: 4, fontSize: 12 }}>📡 ACM Mission Status</div>
      <div>🕐 Sim time: {metrics.sim_time ? new Date(metrics.sim_time).toUTCString().slice(17, 25) : "—"}</div>
      <div>⏱  Sim epoch: {metrics.sim_epoch.toFixed(0)}s</div>
      <div>💥 Collisions: {metrics.total_collisions}</div>
      <div>🚀 Maneuvers executed: {metrics.total_maneuvers_executed}</div>
      <div>⏳ Pending burns: {metrics.pending_burns}</div>
    </div>
  );
}

// ── SatelliteScene ─────────────────────────────────────────────────────────────
export default function SatelliteScene() {
  const [satData,    setSatData]    = useState<ShellData[]>([]);
  const [debrisData, setDebrisData] = useState<ShellData[]>([]);
  const [alerts,     setAlerts]     = useState<{ message: string }[]>([]);
  const [metrics,    setMetrics]    = useState<Metrics>({
    total_collisions: 0, total_maneuvers_executed: 0,
    pending_burns: 0, sim_epoch: 0, sim_time: null,
  });
  const [offline,    setOffline]    = useState(false);
  const [celestrakLoading, setCelestrakLoading] = useState(false);

  const stepFailCountRef = useRef(0);
  const MAX_STEP_FAILS   = 3;
  const SIM_STEP_INTERVAL_MS = 5000;

  useEffect(() => {
    const loadSnapshot = async () => {
      try {
        const result = await fetchSnapshot();
        setSatData(result.satellites    || []);
        setDebrisData(result.debris     || []);
        setAlerts(result.cdm_warnings   || []);
        setMetrics(result.metrics);
        setOffline(false);
        stepFailCountRef.current = 0;
      } catch {
        setOffline(true);
      }
    };

    loadSnapshot();
    const snapshotInterval = setInterval(loadSnapshot, 1000);

    // FIX: step interval self-pauses after repeated backend failures
    const stepInterval = setInterval(async () => {
      if (stepFailCountRef.current >= MAX_STEP_FAILS) return;
      try {
        await stepSimulation(10);
        stepFailCountRef.current = 0;
      } catch {
        stepFailCountRef.current += 1;
        if (stepFailCountRef.current >= MAX_STEP_FAILS) {
          console.warn("ACM: backend step failing — pausing step interval");
          setOffline(true);
        }
      }
    }, SIM_STEP_INTERVAL_MS);

    return () => {
      clearInterval(snapshotInterval);
      clearInterval(stepInterval);
    };
  }, []);

  const handleLoadCelestrak = async () => {
    setCelestrakLoading(true);
    try {
      const res = await loadCelestrakData(500, 50);
      console.log("CelesTrak loaded:", res);
    } catch (e) {
      console.error("CelesTrak load failed:", e);
    } finally {
      setCelestrakLoading(false);
    }
  };

  return (
    <div style={{ width: "100vw", height: "100vh", background: "#000", position: "relative" }}>
      <Alerts alerts={alerts} />
      <MetricsHUD metrics={metrics} offline={offline} />

      {/* CelesTrak load button */}
      <button
        onClick={handleLoadCelestrak}
        disabled={celestrakLoading}
        style={{
          position: "absolute", top: 10, left: 10, zIndex: 100,
          background: "rgba(10,10,20,0.85)", border: "1px solid rgba(0,200,255,0.4)",
          color: "#00ccff", padding: "6px 12px", borderRadius: 6,
          fontFamily: "monospace", fontSize: 12, cursor: "pointer",
        }}
      >
        {celestrakLoading ? "Loading…" : "🛰️ Load CelesTrak"}
      </button>

      <Canvas flat>
        <color attach="background" args={["#010101"]} />
        <PerspectiveCamera makeDefault position={[0, 0, 5]} />
        <ambientLight intensity={0.8} />
        <pointLight position={[10, 10, 10]} intensity={2.0} />
        <directionalLight position={[-5, 3, 5]} intensity={1.5} />

        <Suspense
          fallback={
            <mesh>
              <sphereGeometry args={[1, 32, 32]} />
              <meshBasicMaterial color="#112244" wireframe />
            </mesh>
          }
        >
          <Earth />
        </Suspense>

        <OrbitalShell data={satData} />
        <DebrisShell  data={debrisData} />

        <Stars radius={300} count={7000} factor={7} saturation={0} fade speed={1} />
        <OrbitControls enablePan={false} minDistance={1.5} maxDistance={15} />
      </Canvas>
    </div>
  );
}