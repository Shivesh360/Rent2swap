# Spline 3D Performance & Optimization Guide

WebGL and 3D scenes demand significant GPU, CPU, and memory resources. Follow these architectural rules to keep your application running at 60 FPS while preserving mobile battery life.

---

## 1. Scene Optimization in Spline Editor

Before exporting `.splinecode`, optimize the scene assets:

1. **Polygon Count**: Keep total triangles under 50,000 for mobile web performance. Decimate high-poly meshes.
2. **Lights & Shadows**:
   - Limit real-time shadow-casting lights to 1 or 2 maximum.
   - Prefer baked lighting / ambient occlusion maps over dynamic directional shadows where possible.
3. **Texture Sizes**:
   - Limit textures to 1024x1024 (or 512x512 for secondary materials).
   - Use compressed JPG / WebP textures instead of uncompressed PNGs.
4. **Post-Processing**:
   - Disable heavy screen-space effects (e.g. intense Bloom, Chromatic Aberration, Depth of Field) for mobile exports.

---

## 2. Dynamic Device Pixel Ratio (DPR) Scaling

High-DPI displays (Retina screens, modern phones at 3x or 4x DPR) can cripple WebGL fill-rate by rendering 9x to 16x more pixels. Always clamp the canvas DPR:

```javascript
// Clamp device pixel ratio to maximum of 1.5 or 2.0
const clampedDpr = Math.min(window.devicePixelRatio || 1, 1.5);
```

In `@splinetool/runtime`:
```javascript
const canvas = document.getElementById('canvas3d');
const app = new Application(canvas, {
  renderOnDemand: true, // Only render frames when scene changes or interacts
});
```

---

## 3. Viewport-Based Lazy Loading (IntersectionObserver)

Do not download 2MB–10MB `.splinecode` scenes or initialize WebGL contexts until the user scrolls near the element:

```typescript
import React, { useState, useEffect, useRef } from 'react';

export function LazySpline({ sceneUrl }: { sceneUrl: string }) {
  const [shouldLoad, setShouldLoad] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShouldLoad(true);
          observer.disconnect();
        }
      },
      { rootMargin: '200px' } // Preload 200px before scrolling into view
    );

    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={containerRef} className="w-full h-[500px] relative bg-neutral-900 rounded-xl">
      {shouldLoad ? (
        <Spline scene={sceneUrl} />
      ) : (
        <div className="w-full h-full flex items-center justify-center text-neutral-500">
          Loading 3D asset...
        </div>
      )}
    </div>
  );
}
```

---

## 4. Pausing Render Loop When Offscreen

When a user scrolls past an active 3D canvas, pause rendering or disconnect event listeners to save battery:

```javascript
const observer = new IntersectionObserver(([entry]) => {
  if (entry.isIntersecting) {
    app.play(); // Resume animation loop
  } else {
    app.stop(); // Halt requestAnimationFrame render loop
  }
});
observer.observe(canvas);
```

---

## 5. WebGL Context Teardown & Garbage Collection

Browsers impose a strict limit on simultaneous WebGL contexts (usually 8 to 16). Failing to dispose of canvases during route navigation leads to `WebGL: CONTEXT_LOST_WEBGL`.

Always call `dispose()` when unmounting:
```typescript
useEffect(() => {
  let appInstance: Application | null = null;

  async function init() {
    appInstance = new Application(canvasRef.current);
    await appInstance.load(sceneUrl);
  }

  init();

  return () => {
    if (appInstance && typeof appInstance.dispose === 'function') {
      appInstance.dispose();
    }
  };
}, [sceneUrl]);
```

---

## 6. Accessibility & Reduced Motion

Respect user OS accessibility preferences:

```typescript
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

if (prefersReducedMotion) {
  // Option A: Render a static WebP fallback instead of the 3D canvas
  // Option B: Stop automatic rotation / camera transitions
}
```
