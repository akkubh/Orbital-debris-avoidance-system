import React, { useState, useEffect, Suspense } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, Stars, useTexture, PerspectiveCamera } from '@react-three/drei';
import { OrbitalShell } from './OrbitalShell';
import { fetchSnapshot } from '../services/api';

// 1. STABLE EARTH COMPONENT
function Earth() {
  // Reliable texture from Three.js official examples
  const texture = useTexture('https://raw.githubusercontent.com/mrdoob/three.js/master/examples/textures/planets/earth_atmos_2048.jpg');

  return (
    <mesh rotation={[0, Math.PI / 2, 0]}>
      <sphereGeometry args={[1, 64, 64]} />
      {/* meshStandardMaterial looks realistic but needs light to be seen */}
      <meshStandardMaterial 
        map={texture} 
        roughness={0.7} 
        metalness={0.2} 
      />
    </mesh>
  );
}

// 2. MAIN SCENE COMPONENT
export default function SatelliteScene() {
  const [satData, setSatData] = useState([]);

  useEffect(() => {
    const loadData = async () => {
      try {
        const result = await fetchSnapshot();
        // Fallback to empty array to prevent .map errors
        setSatData(result.satellites || []);
      } catch (e) {
        console.error("API Fetch Error:", e);
      }
    };
    
    loadData();
    const interval = setInterval(loadData, 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ width: '100vw', height: '100vh', background: '#000' }}>
      <Canvas flat>
        {/* Background color of the universe */}
        <color attach="background" args={['#010101']} />
        
        <PerspectiveCamera makeDefault position={[0, 0, 5]} />
        
        {/* 3. LIGHTING (Essential for the Earth) */}
        <ambientLight intensity={0.8} />
        <pointLight position={[10, 10, 10]} intensity={2.0} />
        <directionalLight position={[-5, 3, 5]} intensity={1.5} />

        {/* 4. EARTH WITH LOADING STATE */}
        <Suspense fallback={
          <mesh>
            <sphereGeometry args={[1, 32, 32]} />
            <meshBasicMaterial color="#112244" wireframe />
          </mesh>
        }>
          <Earth />
        </Suspense>

        {/* 5. THE SATELLITES LAYER */}
        <OrbitalShell data={satData} />

        {/* 6. STARS & CONTROLS */}
        <Stars 
          radius={300} 
          count={7000} 
          factor={7} 
          saturation={0} 
          fade 
          speed={1} 
        />
        
        <OrbitControls 
          enablePan={false} 
          minDistance={1.5} 
          maxDistance={15} 
        />
      </Canvas>
    </div>
  );
}