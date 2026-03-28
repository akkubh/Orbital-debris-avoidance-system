import { Line } from "@react-three/drei";

// BUG 11 FIX: the Line component from @react-three/drei expects each point to
// be either a THREE.Vector3 or a plain [x, y, z] array of finite numbers.
// If generatePaths produced NaN coordinates (which it could when sat.x/y/z
// were 0 on first render before the snapshot arrived), drei would throw:
//   "THREE.BufferGeometry: NaN in position attribute"
// and crash the canvas.
//
// Fix: filter out any path that contains non-finite coordinates before
// passing to <Line>.
function isValidPoint(p) {
  return Array.isArray(p) && p.length === 3 && p.every(Number.isFinite);
}

export default function ManeuverPath({ paths }) {
  if (!paths || paths.length === 0) return null;

  return (
    <>
      {paths.map((path, i) => {
        if (!path || path.length < 2) return null;

        // BUG 11 FIX: drop any path whose points contain NaN / Infinity
        const validPoints = path.filter(isValidPoint);
        if (validPoints.length < 2) return null;

        return (
          <Line
            key={i}
            points={validPoints}
            color="cyan"
            lineWidth={2}
          />
        );
      })}
    </>
  );
}