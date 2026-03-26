export default function Debris({ debris }) {
  return (
    <>
      {debris.map((d, i) => (
        <mesh key={i} position={[d.x, d.y, d.z]}>
          <sphereGeometry args={[0.03, 8, 8]} />
          <meshStandardMaterial color="red" />
        </mesh>
      ))}
    </>
  );
}