# Vanilla JavaScript & HTML Integration Guide for Spline 3D

This guide covers embedding Spline 3D scenes into vanilla HTML, template engines (Jinja2, Django, Blade, EJS), or plain JavaScript frontends.

---

## 1. Method 1: Spline Viewer Web Component (Simplest)

The official `<spline-viewer>` custom element handles canvas creation, resize events, and asset streaming automatically.

### Basic HTML Embed
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Spline 3D Vanilla</title>
  <!-- Load Spline Viewer Web Component script -->
  <script type="module" src="https://unpkg.com/@splinetool/viewer@latest/build/spline-viewer.js"></script>
  <style>
    .spline-container {
      width: 100%;
      height: 500px;
      position: relative;
      background: #0f172a;
      border-radius: 1rem;
      overflow: hidden;
    }
    spline-viewer {
      width: 100%;
      height: 100%;
      display: block;
    }
  </style>
</head>
<body>

  <div class="spline-container">
    <spline-viewer 
      url="https://prod.spline.design/sample-scene/scene.splinecode"
      loading-anim-type="spinner-small-dark">
    </spline-viewer>
  </div>

  <script>
    const viewer = document.querySelector('spline-viewer');
    
    // Listen for scene load completion
    viewer.addEventListener('load-complete', (e) => {
      console.log('Spline scene fully loaded!', e.detail);
      // Access the internal Spline Application instance
      const spline = viewer.shadowRoot.querySelector('canvas')?.__spline || viewer.spline;
    });
  </script>
</body>
</html>
```

### Viewer Attributes Reference
- `url`: (Required) Public URL to the `.splinecode` export.
- `loading-anim-type`: `'spinner-small-light'`, `'spinner-small-dark'`, or `'none'`.
- `events-target`: `'window'` or `'global'` to capture pointer events beyond canvas boundary.

---

## 2. Method 2: `@splinetool/runtime` with `<canvas>` (Full Control)

For fine-grained control over the WebGL context, frame rendering, and bundle optimization, use `@splinetool/runtime`.

### Setup with ES Modules
```html
<canvas id="canvas3d" style="width: 100%; height: 600px; display: block;"></canvas>

<script type="module">
  import { Application } from 'https://unpkg.com/@splinetool/runtime@latest/build/runtime.js';

  const canvas = document.getElementById('canvas3d');
  const app = new Application(canvas);

  app.load('https://prod.spline.design/sample-scene/scene.splinecode')
    .then(() => {
      console.log('Scene loaded successfully');
      
      // Find objects
      const cube = app.findObjectByName('MainCube');
      
      // Listen to clicks inside the 3D scene
      app.addEventListener('mouseDown', (e) => {
        if (e.target.name === 'MainCube') {
          console.log('Cube clicked!');
        }
      });
    })
    .catch((err) => {
      console.error('Failed to load Spline scene:', err);
    });
</script>
```

---

## 3. Integrating with Template Engines (Flask / Jinja2)

In template engines like Flask / Jinja2 (as in `Rent It Bro`):

```html
<!-- Inside templates/base.html or templates/index.html -->
{% block content %}
<div class="card shadow-sm border-0 mb-4 overflow-hidden">
  <div class="card-body p-0 position-relative" style="height: 450px;">
    <!-- 3D Canvas / Viewer -->
    <script type="module" src="https://unpkg.com/@splinetool/viewer@latest/build/spline-viewer.js"></script>
    <spline-viewer url="https://prod.spline.design/sample-id/scene.splinecode"></spline-viewer>
    
    <!-- Hero Text Overlay -->
    <div class="position-absolute top-50 start-0 translate-middle-y ps-5 text-white pe-none" style="z-index: 10;">
      <h1 class="display-5 fw-bold">Explore Campus Rentals</h1>
      <p class="lead">Peer-to-peer textbooks, electronics & lab essentials in 3D.</p>
      <a href="/marketplace" class="btn btn-primary pe-auto">Browse Marketplace</a>
    </div>
  </div>
</div>
{% endblock %}
```

---

## 4. Teardown & Memory Management in SPAs
When navigating away or swapping DOM elements in single-page vanilla architectures, call `app.dispose()`:

```javascript
// Clean up WebGL resources
if (app && typeof app.dispose === 'function') {
  app.dispose();
}
```
