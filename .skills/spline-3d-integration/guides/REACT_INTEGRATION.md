# React Integration Guide for Spline 3D

This guide details how to integrate Spline 3D scenes seamlessly into React applications (Vite, Next.js App/Pages router, or standard SPA).

---

## 1. Installation

Install the official React binding and the core WebGL runtime:

```bash
npm install @splinetool/react-spline @splinetool/runtime
```

---

## 2. Vite / Standard React SPA Integration

In Vite, Spline can be rendered directly using `<Spline scene="..." />`.

```tsx
import React, { useRef } from 'react';
import Spline from '@splinetool/react-spline';
import type { Application } from '@splinetool/runtime';

export default function HeroScene() {
  const splineRef = useRef<Application | null>(null);

  function onLoad(splineApp: Application) {
    splineRef.current = splineApp;
    console.log('Spline scene initialized:', splineApp);
  }

  return (
    <div className="relative w-full h-[600px] overflow-hidden rounded-2xl bg-neutral-950">
      <Spline
        scene="https://prod.spline.design/your-scene-id/scene.splinecode"
        onLoad={onLoad}
      />
    </div>
  );
}
```

---

## 3. Next.js (SSR / Server Components) Safety

Because Spline relies on browser WebGL context (`window`, `HTMLCanvasElement`), it **cannot** be rendered on the server. Always disable SSR using dynamic imports:

### Next.js App Router (React Server Components)
Create a client component:
```tsx
'use client';

import dynamic from 'next/dynamic';

// Dynamically import Spline with SSR disabled
const Spline = dynamic(() => import('@splinetool/react-spline'), {
  ssr: false,
  loading: () => (
    <div className="w-full h-full flex items-center justify-center bg-neutral-900 animate-pulse text-neutral-400">
      Loading 3D Experience...
    </div>
  ),
});

export default function SceneClientWrapper({ sceneUrl }: { sceneUrl: string }) {
  return <Spline scene={sceneUrl} />;
}
```

---

## 4. Programmatic Control & Object Traversal

To manipulate 3D elements inside your scene:

```tsx
import { useState, useRef } from 'react';
import Spline from '@splinetool/react-spline';
import type { Application, SPEObject } from '@splinetool/runtime';

export function InteractiveCarConfigurator() {
  const [selectedColor, setSelectedColor] = useState('#ff0055');
  const splineRef = useRef<Application | null>(null);

  function onLoad(spline: Application) {
    splineRef.current = spline;
  }

  function handleTriggerSpin() {
    if (!splineRef.current) return;
    // Emit Spline event defined in editor (e.g. 'mouseDown', 'mouseHover')
    splineRef.current.emitEvent('mouseDown', 'SpinButton');
  }

  function handleMoveObject() {
    if (!splineRef.current) return;
    const body = splineRef.current.findObjectByName('CarBody');
    if (body) {
      body.position.y += 10;
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="relative w-full h-[500px]">
        <Spline
          scene="https://prod.spline.design/sample/scene.splinecode"
          onLoad={onLoad}
          onSplineMouseDown={(e) => {
            console.log('Object clicked in 3D:', e.target.name);
          }}
        />
      </div>
      <div className="flex gap-2">
        <button onClick={handleTriggerSpin} className="px-4 py-2 bg-blue-600 rounded text-white">
          Spin Model
        </button>
        <button onClick={handleMoveObject} className="px-4 py-2 bg-neutral-700 rounded text-white">
          Elevate
        </button>
      </div>
    </div>
  );
}
```

---

## 5. Event Callbacks Reference

`<Spline />` provides built-in event listeners:

| Prop | Type | Description |
|---|---|---|
| `onLoad` | `(spline: Application) => void` | Fires when scene and assets are loaded and WebGL initialized. |
| `onSplineMouseDown` | `(e: SplineEvent) => void` | Fires when user clicks an object in the 3D scene. |
| `onSplineMouseUp` | `(e: SplineEvent) => void` | Fires when user releases mouse over an object. |
| `onSplineMouseHover` | `(e: SplineEvent) => void` | Fires when cursor enters an object's boundary. |
| `onSplineKeyDown` | `(e: SplineEvent) => void` | Fires when keydown occurs while canvas is focused. |
| `onSplineKeyUp` | `(e: SplineEvent) => void` | Fires when keyup occurs. |

---

## 6. Sizing & Viewport Layering Rules

1. **Overlay UI**: Place UI layers above the 3D canvas using `pointer-events-none` on parent containers and `pointer-events-auto` on clickable interactive buttons.
2. **Aspect Ratios**: Avoid setting fixed pixel widths when building responsive headers. Use responsive Tailwind classes like `w-full h-[60vh] min-h-[400px] max-h-[800px]`.
3. **No Duplicate Canvases**: Avoid rendering multiple full-screen Spline canvases on the same page. Consolidate into a single shared scene or unmount inactive views.
