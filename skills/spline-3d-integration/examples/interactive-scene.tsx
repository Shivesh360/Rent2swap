import React, { useState, useRef } from 'react';
import Spline from '@splinetool/react-spline';
import type { Application, SPEObject } from '@splinetool/runtime';

interface ProductCustomizerProps {
  sceneUrl?: string;
}

export function InteractiveScene({
  sceneUrl = 'https://prod.spline.design/6Wq1Q7YGyM-iab9i/scene.splinecode',
}: ProductCustomizerProps) {
  const splineRef = useRef<Application | null>(null);
  const [activeColor, setActiveColor] = useState<string>('emerald');
  const [activePart, setActivePart] = useState<string>('Base');
  const [statusMessage, setStatusMessage] = useState<string>('Ready to interact');

  // Callback once the 3D scene finishes loading
  function handleLoad(spline: Application) {
    splineRef.current = spline;
    setStatusMessage('3D Scene loaded. Click any object to interact!');
  }

  // Handle object selection from 3D viewport
  function handleSplineMouseDown(e: any) {
    if (e.target?.name) {
      setActivePart(e.target.name);
      setStatusMessage(`Selected 3D Object: ${e.target.name}`);
    }
  }

  // Trigger Spline-defined native state transitions
  function triggerAnimation(actionName: string) {
    if (!splineRef.current) return;
    try {
      splineRef.current.emitEvent('mouseDown', actionName);
      setStatusMessage(`Triggered action: ${actionName}`);
    } catch (err) {
      console.warn('Action not bound in Spline scene:', actionName);
    }
  }

  // Direct transform manipulation
  function rotateObject() {
    if (!splineRef.current) return;
    const obj: SPEObject | undefined = splineRef.current.findObjectByName(activePart);
    if (obj) {
      obj.rotation.y += Math.PI / 4;
      setStatusMessage(`Rotated ${activePart} by 45 degrees`);
    } else {
      setStatusMessage(`Object "${activePart}" not found in scene tree`);
    }
  }

  // Reset Camera
  function resetCamera() {
    if (!splineRef.current) return;
    splineRef.current.emitEvent('keyDown', 'ResetCamera');
    setStatusMessage('Camera reset to default view');
  }

  return (
    <div className="w-full max-w-5xl mx-auto p-6 bg-neutral-900 border border-neutral-800 rounded-2xl shadow-2xl flex flex-col gap-6">
      {/* Header & Status */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-neutral-800 pb-4">
        <div>
          <h2 className="text-xl font-bold text-white tracking-tight">3D Interactive Viewer</h2>
          <p className="text-sm text-neutral-400">Direct two-way state synchronization with Spline WebGL</p>
        </div>
        <div className="px-3 py-1.5 rounded-full bg-neutral-800 border border-neutral-700 text-xs font-mono text-emerald-400">
          {statusMessage}
        </div>
      </div>

      {/* 3D Scene Viewport */}
      <div className="relative w-full h-[520px] rounded-xl overflow-hidden bg-neutral-950 border border-neutral-800">
        <Spline
          scene={sceneUrl}
          onLoad={handleLoad}
          onSplineMouseDown={handleSplineMouseDown}
        />

        {/* Viewport Overlay Controls */}
        <div className="absolute top-4 right-4 flex flex-col gap-2 z-10 pointer-events-auto">
          <button
            onClick={resetCamera}
            className="p-2.5 bg-neutral-900/80 backdrop-blur hover:bg-neutral-800 border border-neutral-700 rounded-lg text-xs font-medium text-white shadow-lg transition"
            title="Reset Viewpoint"
          >
            Reset Camera
          </button>
        </div>
      </div>

      {/* Control Panel */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2">
        {/* Selected Part */}
        <div className="p-4 rounded-xl bg-neutral-950 border border-neutral-800 flex flex-col gap-2">
          <span className="text-xs uppercase tracking-wider text-neutral-500 font-semibold">Active Selection</span>
          <span className="text-lg font-bold text-white">{activePart}</span>
          <button
            onClick={rotateObject}
            className="mt-2 w-full py-2 px-3 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 text-xs font-medium rounded-lg transition"
          >
            Rotate Selection 45°
          </button>
        </div>

        {/* State Presets */}
        <div className="p-4 rounded-xl bg-neutral-950 border border-neutral-800 flex flex-col gap-2">
          <span className="text-xs uppercase tracking-wider text-neutral-500 font-semibold">Scene Triggers</span>
          <div className="grid grid-cols-2 gap-2 mt-1">
            <button
              onClick={() => triggerAnimation('Explode')}
              className="py-2 px-3 bg-indigo-600/30 hover:bg-indigo-600/50 border border-indigo-500/50 text-indigo-200 text-xs font-medium rounded-lg transition"
            >
              Explode View
            </button>
            <button
              onClick={() => triggerAnimation('Assemble')}
              className="py-2 px-3 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 text-xs font-medium rounded-lg transition"
            >
              Reassemble
            </button>
          </div>
        </div>

        {/* Color Palette Switcher */}
        <div className="p-4 rounded-xl bg-neutral-950 border border-neutral-800 flex flex-col gap-2">
          <span className="text-xs uppercase tracking-wider text-neutral-500 font-semibold">Material Preset</span>
          <div className="flex gap-2 mt-2">
            {[
              { id: 'emerald', bg: 'bg-emerald-500' },
              { id: 'indigo', bg: 'bg-indigo-500' },
              { id: 'amber', bg: 'bg-amber-500' },
              { id: 'rose', bg: 'bg-rose-500' },
            ].map((c) => (
              <button
                key={c.id}
                onClick={() => {
                  setActiveColor(c.id);
                  triggerAnimation(`Color_${c.id}`);
                }}
                className={`w-8 h-8 rounded-full ${c.bg} transition transform ${
                  activeColor === c.id ? 'ring-2 ring-white scale-110' : 'opacity-70 hover:opacity-100'
                }`}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default InteractiveScene;
