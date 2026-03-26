import { useFrame } from "@react-three/fiber";
import { useRef } from "react";

export default function Satellites({ satellites }) {
  const group = useRef();
  useFrame(() => {
    group.current.rotation.y += 0.002;
  });
  return (
    <group ref={group}>
      {satellites.map((sat, i) => (
        <mesh key={i} position={[sat.x, sat.y, sat.z]}>
          <sphereGeometry args={[0.05, 16, 16]} />
          <meshStandardMaterial color="yellow" />
        </mesh>
      ))}
    </group>
  );
}