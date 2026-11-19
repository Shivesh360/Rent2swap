---
name: spline-3d-integration
description: Architect, integrate, and optimize interactive 3D scenes using Spline (@splinetool/runtime and @splinetool/react-spline) for React and vanilla web applications. Use when embedding 3D graphics, interactive models, hero scenes, 3D product visualizers, or event-driven 3D animations.
---

# Spline 3D Integration Skill

This skill provides comprehensive instructions, architectural guidelines, and production-ready code patterns for embedding interactive 3D scenes built with [Spline](https://spline.design) into React (Vite/Next.js) and vanilla web applications.

---

## 1. Core Capabilities & When to Use

Use Spline 3D integration when the application requires:
- **Interactive 3D Hero Sections**: Dynamic visual centerpieces reacting to cursor movement, scroll, or hover.
- **Product Visualizers & Showcase**: 360° rotating products, interactive feature hotspots, color-switching models.
- **Gamified & Micro-Interactions**: Interactive 3D buttons, floating badges, mascot reactions, or celebratory animations.
- **Canvas Overlay Animations**: Lightweight 3D physics or particles layered behind or around typography and UI cards.

---

## 2. Package Installation & Dependencies

### React Applications (Vite, Next.js, CRA)
```bash
npm install @splinetool/react-spline @splinetool/runtime
```

### Vanilla JavaScript / HTML Applications
```bash
npm install @splinetool/runtime
```
*Or use the CDN script/web-component:*
```html
<script type="module" src="https://unpkg.com/@splinetool/viewer@latest/build/spline-viewer.js"></script>
```

---

## 3. Integration Patterns

### Pattern A: React Production Wrapper (Recommended)
Always wrap Spline scenes with:
1. **Lazy Loading (`React.lazy` + `Suspense`)**: Avoid bundling large runtime code in the initial bundle.
2. **IntersectionObserver**: Only mount and load the `.splinecode` asset when the element scrolls into or near the viewport.
3. **Graceful Loading Skeleton / Spinner**: Display an aesthetically matched loader to avoid blank layout shifts.
4. **WebGL Disposal on Unmount**: Clean up canvas listeners and free GPU memory.

*See `examples/react-spline-wrapper.tsx` for the ready-to-use component.*

### Pattern B: Vanilla / HTML Embed
Embed Spline directly into static or template-driven web apps (e.g. Jinja2, PHP, plain HTML) using either:
1. The `<spline-viewer url="...">` custom element.
2. The `@splinetool/runtime` `Application` class bound to a `<canvas>` element.

*See `guides/VANILLA_INTEGRATION.md` and `examples/vanilla-embed.html`.*

---

## 4. Interactivity & Programmatic Control

Spline provides bidirectional communication between the web UI and the 3D scene:

### Accessing Objects & Triggering Events
```typescript
import { Application, SPEObject } from '@splinetool/runtime';

function handleSplineLoad(spline: Application) {
  // 1. Find 3D objects by name configured in the Spline Editor
  const cube: SPEObject | undefined = spline.findObjectByName('InteractiveCube');
  
  // 2. Programmatically alter transform or properties
  if (cube) {
    cube.position.y += 50;
    cube.rotation.x = Math.PI / 4;
    cube.scale.set(1.2, 1.2, 1.2);
  }

  // 3. Trigger Spline native state transitions / events
  spline.emitEvent('mouseHover', 'InteractiveCube');
  spline.emitEvent('mouseDown', 'StartButton');

  // 4. Listen to user interactions inside the 3D canvas
  spline.addEventListener('mouseDown', (e) => {
    if (e.target.name === 'OrderButton') {
      console.log('3D button clicked!');
    }
  });
}
```

---

## 5. Viewport, Layout & Responsive Design

- **Containment**: Always place the Spline canvas inside a parent `div` with explicit dimensions (`relative w-full h-[500px]` or `aspect-[16/9]`).
- **Pointer Events**:
  - If the 3D scene is a background element behind text, add `pointer-events-none` to the canvas and ensure foreground buttons remain clickable.
  - If only specific objects are interactive, ensure the container handles touch scrolling appropriately on mobile (`touch-action: pan-y`).
- **Mobile Fallbacks**:
  - On low-powered mobile devices, consider rendering a static WebP snapshot or lowering pixel density to `Math.min(window.devicePixelRatio, 1.5)`.

---

## 6. Directory Guide

- `guides/REACT_INTEGRATION.md`: Step-by-step React and Next.js guide with SSR safety.
- `guides/VANILLA_INTEGRATION.md`: Vanilla JavaScript, CDN, and template engine embedding.
- `guides/PERFORMANCE.md`: GPU optimization, asset compression, lazy loading, and battery preservation.
- `examples/react-spline-wrapper.tsx`: Production wrapper with Suspense, fallback, and error handling.
- `examples/interactive-scene.tsx`: Interactive React component demonstrating 3D object manipulation and UI state synchronization.
- `examples/vanilla-embed.html`: Standalone HTML page with Spline runtime and custom control buttons.
