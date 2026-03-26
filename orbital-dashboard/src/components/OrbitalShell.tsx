import { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

// This matches the new structure we put in api.js
interface SatelliteData {
  id: string;
  scaledPosition: [number, number, number];
}

export function OrbitalShell({ data }: { data: SatelliteData[] }) {
  const meshRef = useRef<THREE.InstancedMesh>(null!);
  const tempObject = new THREE.Object3D();

  useFrame(() => {
    if (!meshRef.current || !data || data.length === 0) return;

    data.forEach((sat, i) => {
      // Use the 'scaledPosition' we created in api.js
      const [x, y, z] = sat.scaledPosition;
      tempObject.position.set(x, y, z);
      tempObject.updateMatrix();
      meshRef.current.setMatrixAt(i, tempObject.matrix);
    });

    meshRef.current.instanceMatrix.needsUpdate = true;
  });

  return (
    // We set the limit to 10,000 for your stress test goal
    <instancedMesh ref={meshRef} args={[null, null, 10000]}>
      <sphereGeometry args={[0.007, 8, 8]} /> 
      <meshBasicMaterial color="#00ffcc" />
    </instancedMesh>
  );

}

<meshBasicMaterial 
  color="#00ffcc" 
  transparent={true}
  opacity={0.9}
/>