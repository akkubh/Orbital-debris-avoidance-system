import { useRef, useEffect } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

interface SatelliteData {
  id: string;
  scaledPosition: [number, number, number];
  status?: string;
  pending_burns?: number;
}

const STATUS_COLORS: Record<string, THREE.Color> = {
  NOMINAL:     new THREE.Color(0x00ffee),
  MANEUVERING: new THREE.Color(0xffee00),
  EOL:         new THREE.Color(0xff8800),
  DEAD:        new THREE.Color(0xff2200),
};

function getColor(sat: SatelliteData): THREE.Color {
  if ((sat.pending_burns ?? 0) > 0 && sat.status !== 'DEAD' && sat.status !== 'EOL') {
    return STATUS_COLORS.MANEUVERING;
  }
  return STATUS_COLORS[sat.status ?? 'NOMINAL'] ?? STATUS_COLORS.NOMINAL;
}

/** Generate a soft circular gradient canvas texture so points render as circles. */
function makeCircleTexture(size = 64): THREE.Texture {
  const canvas  = document.createElement('canvas');
  canvas.width  = size;
  canvas.height = size;
  const ctx     = canvas.getContext('2d')!;
  const r       = size / 2;
  const grad    = ctx.createRadialGradient(r, r, 0, r, r, r);
  grad.addColorStop(0.0,  'rgba(255,255,255,1.0)');
  grad.addColorStop(0.5,  'rgba(255,255,255,0.9)');
  grad.addColorStop(0.85, 'rgba(255,255,255,0.3)');
  grad.addColorStop(1.0,  'rgba(255,255,255,0.0)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, size, size);
  const tex        = new THREE.CanvasTexture(canvas);
  tex.needsUpdate  = true;
  return tex;
}

const MAX_SATS      = 10000;
const CIRCLE_TEX    = makeCircleTexture(64);

export function OrbitalShell({ data }: { data: SatelliteData[] }) {
  // FIX: useRef so useFrame always reads the latest data prop (not stale closure)
  const dataRef = useRef<SatelliteData[]>(data);
  dataRef.current = data;

  const pointsRef = useRef<THREE.Points>(null!);
  const posAttr   = useRef<THREE.BufferAttribute | null>(null);
  const colAttr   = useRef<THREE.BufferAttribute | null>(null);

  useEffect(() => {
    const positions = new Float32Array(MAX_SATS * 3);
    const colors    = new Float32Array(MAX_SATS * 3);

    // Default all to cyan so satellites are visible even before first data poll
    for (let i = 0; i < MAX_SATS; i++) {
      colors[i * 3]     = 0.0;
      colors[i * 3 + 1] = 1.0;
      colors[i * 3 + 2] = 0.93;
    }

    const geo = new THREE.BufferGeometry();
    posAttr.current = new THREE.BufferAttribute(positions, 3);
    colAttr.current = new THREE.BufferAttribute(colors, 3);
    posAttr.current.setUsage(THREE.DynamicDrawUsage);
    colAttr.current.setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute('position', posAttr.current);
    geo.setAttribute('color',    colAttr.current);
    geo.setDrawRange(0, 0);

    if (pointsRef.current) {
      pointsRef.current.geometry.dispose();
      pointsRef.current.geometry = geo;
    }
  }, []);

  useFrame(() => {
    const d = dataRef.current;
    if (!pointsRef.current || !posAttr.current || !colAttr.current) return;

    const count  = Math.min(d.length, MAX_SATS);
    const posArr = posAttr.current.array as Float32Array;
    const colArr = colAttr.current.array as Float32Array;

    for (let i = 0; i < count; i++) {
      const sat       = d[i];
      const [x, y, z] = sat.scaledPosition;
      posArr[i * 3]     = x;
      posArr[i * 3 + 1] = y;
      posArr[i * 3 + 2] = z;
      const c           = getColor(sat);
      colArr[i * 3]     = c.r;
      colArr[i * 3 + 1] = c.g;
      colArr[i * 3 + 2] = c.b;
    }

    posAttr.current.needsUpdate = true;
    colAttr.current.needsUpdate = true;
    pointsRef.current.geometry.setDrawRange(0, count);
  });

  return (
    <points ref={pointsRef}>
      <bufferGeometry />
      <pointsMaterial
        vertexColors
        map={CIRCLE_TEX}       /* circular soft-glow dot instead of square */
        alphaMap={CIRCLE_TEX}
        alphaTest={0.01}
        sizeAttenuation={false}
        size={8}
        depthWrite={false}
        transparent
      />
    </points>
  );
}