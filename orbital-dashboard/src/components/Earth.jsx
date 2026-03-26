import { useLoader } from "@react-three/fiber";
import { TextureLoader } from "three";

export default function Earth() {
  const texture = useLoader(
    TextureLoader,
    "https://unpkg.com/three-globe/example/img/earth-day.jpg"
  );
  return (
    <mesh>
      <sphereGeometry args={[2, 64, 64]} />
      <meshStandardMaterial map={texture} />
    </mesh>
  );
}