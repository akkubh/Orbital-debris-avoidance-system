import { useRef, useEffect } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

interface SatelliteData {
  id: string;
  scaledPosition: [number, number, number];
  status?: string;
  pending_burns?: number;
}

// Colour map:
//   NOMINAL      → cyan    #00ffcc
//   MANEUVERING  → yellow  #ffdd00  (burn queued or executing)
//   EOL          → orange  #ff8800
//   DEAD         → red     #ff2200
//   unknown      → cyan (safe default)
const COLOR_MAP: Record<string, string> = {
  NOMINAL:     '#00ffcc',
  MANEUVERING: '#ffdd00',
  EOL:         '#ff8800',
  DEAD:        '#ff2200',
};

function getColor(sat: SatelliteData): THREE.Color {
  const key = sat.status ?? 'NOMINAL';
  // Also treat "has pending burns" as MANEUVERING even if status hasn't
  // updated yet (backend status lags by one step)
  if ((sat.pending_burns ?? 0) > 0 && key !== 'DEAD' && key !== 'EOL') {
    return new THREE.Color(COLOR_MAP.MANEUVERING);
  }
  return new THREE.Color(COLOR_MAP[key] ?? COLOR_MAP.NOMINAL);
}

const MAX_INSTANCES = 10000;

export function OrbitalShell({ data }: { data: SatelliteData[] }) {
  const meshRef = useRef<THREE.InstancedMesh>(null!);
  const tmpObj  = useRef(new THREE.Object3D());

  // FIX: initialize the instanceColor buffer on first mount so every
  // instance has a valid colour from frame 1.  Without this, Three.js
  // leaves the buffer uninitialised and all satellites render grey/black
  // regardless of what setColorAt writes later.
  useEffect(() => {
    if (!meshRef.current) return;
    // Allocate the colour buffer by setting instance 0 to white.
    // This forces Three.js to create the Float32BufferAttribute immediately.
    meshRef.current.setColorAt(0, new THREE.Color('#ffffff'));
    if (meshRef.current.instanceColor) {
      meshRef.current.instanceColor.needsUpdate = true;
    }
  }, []);

  useFrame(() => {
    if (!meshRef.current || !data || data.length === 0) return;

    const mesh = meshRef.current;

    data.forEach((sat, i) => {
      if (i >= MAX_INSTANCES) return;

      // Position
      const [x, y, z] = sat.scaledPosition;
      tmpObj.current.position.set(x, y, z);
      tmpObj.current.updateMatrix();
      mesh.setMatrixAt(i, tmpObj.current.matrix);

      // Colour
      mesh.setColorAt(i, getColor(sat));
    });

    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) {
      mesh.instanceColor.needsUpdate = true;
    }
  });

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, MAX_INSTANCES]}>
      <sphereGeometry args={[0.008, 8, 8]} />
      {/*
        vertexColors=true enables per-instance colour via setColorAt.
        Without it all instances share the single `color` prop and
        setColorAt has no effect.
      */}
      <meshBasicMaterial vertexColors transparent opacity={0.95} />
    </instancedMesh>
  );
}