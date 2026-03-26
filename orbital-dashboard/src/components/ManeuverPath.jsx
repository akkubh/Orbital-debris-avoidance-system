import { Line } from "@react-three/drei";

export default function ManeuverPath({ paths }) {
  return (
    <>
      {paths.map((path, i) => (
        <Line key={i} points={path} color="cyan" lineWidth={2} />
      ))}
    </>
  );
}