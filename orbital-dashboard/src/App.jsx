import { Canvas } from "@react-three/fiber";
import { OrbitControls, Stars } from "@react-three/drei";
import { useEffect } from "react";

import Earth from "./components/Earth";
import Satellites from "./components/Satellites";
import Debris from "./components/Debris";
import Alerts from "./components/Alerts";
import ManeuverPath from "./components/ManeuverPath";

import { fetchSnapshot } from "./services/api";
import useStore from "./store/useStore";

export default function App() {
  const { satellites, debris, alerts, paths,
          setData, generateAlerts, generatePaths } = useStore();

  useEffect(() => {
    const interval = setInterval(async () => {
      const data = await fetchSnapshot();
      setData(data);
      generateAlerts();
      generatePaths();
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  return (
    <>
      <Canvas camera={{ position: [0, 0, 6] }}>
        <ambientLight intensity={1} />
        <pointLight position={[10, 10, 10]} />
        <Stars />
        <Earth />
        <Satellites satellites={satellites} />
        <Debris debris={debris} />
        <ManeuverPath paths={paths} />
        <OrbitControls />
      </Canvas>

      <Canvas
        camera={{ position: [0, 0, 8] }}
        style={{ width: "100vw", height: "100vh", background: "#000010" }}
      ></Canvas>
      <Alerts alerts={alerts} />
    </>
  );
}