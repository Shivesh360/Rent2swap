/**
 * Rent2Swap - Flowing Blurred Aurora / Wave Background
 * Based on Google Stitch 3D Aurora Wave Export
 * 
 * Smooth, non-intrusive WebGL / Canvas wave shader running behind UI.
 * Pure Vanilla JavaScript + Three.js, lightweight and battery-efficient.
 */

(function () {
  'use strict';

  // Check if reduced motion is preferred
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function initAuroraWave() {
    // If canvas does not exist, create and attach to body
    let canvas = document.getElementById('r2s-aurora-canvas');
    if (!canvas) {
      canvas = document.createElement('canvas');
      canvas.id = 'r2s-aurora-canvas';
      canvas.className = 'r2s-aurora-canvas';
      canvas.style.cssText = [
        'position: fixed',
        'top: 0',
        'left: 0',
        'width: 100vw',
        'height: 100vh',
        'pointer-events: none',
        'z-index: -2',
        'opacity: 0.75',
        'filter: blur(28px) saturate(1.3)',
        'transform: translate3d(0, 0, 0)',
      ].join(';');
      document.body.prepend(canvas);
    }

    if (typeof THREE === 'undefined') {
      console.warn('[Rent2Swap Aurora] Three.js not loaded, falling back to 2D gradient wave.');
      init2DFallback(canvas);
      return;
    }

    try {
      initThreeJSAurora(canvas);
    } catch (err) {
      console.warn('[Rent2Swap Aurora] WebGL context failed, running 2D fallback:', err);
      init2DFallback(canvas);
    }
  }

  function initThreeJSAurora(canvas) {
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 100);
    camera.position.set(0, -1.8, 4.2);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({
      canvas: canvas,
      alpha: true,
      antialias: true,
      powerPreference: 'low-power',
    });

    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    renderer.setPixelRatio(dpr);
    renderer.setSize(window.innerWidth, window.innerHeight);

    // Dynamic wave plane geometry
    const geomWidth = 14;
    const geomHeight = 9;
    const segX = 40;
    const segY = 28;
    const geometry = new THREE.PlaneGeometry(geomWidth, geomHeight, segX, segY);

    // Save base vertex positions
    const posAttr = geometry.attributes.position;
    const originalPositions = new Float32Array(posAttr.array.length);
    originalPositions.set(posAttr.array);

    // Vertex shader simulation via custom colors
    const colors = new Float32Array(posAttr.count * 3);
    const colorA = new THREE.Color('#4c1d95'); // Deep Violet
    const colorB = new THREE.Color('#7c3aed'); // Vibrant Purple
    const colorC = new THREE.Color('#6366f1'); // Electric Indigo
    const colorD = new THREE.Color('#a855f7'); // Lavender Glow
    const colorE = new THREE.Color('#06b6d4'); // Soft Cyan Highlight

    for (let i = 0; i < posAttr.count; i++) {
      const u = (posAttr.getX(i) + geomWidth / 2) / geomWidth;
      const v = (posAttr.getY(i) + geomHeight / 2) / geomHeight;

      // Multi-stop color blending
      const blended = colorA.clone().lerp(colorB, u * 0.7);
      blended.lerp(colorC, v * 0.6);
      if (u > 0.6 && v > 0.4) blended.lerp(colorD, 0.35);
      if (u > 0.8 && v < 0.3) blended.lerp(colorE, 0.25);

      colors[i * 3] = blended.r;
      colors[i * 3 + 1] = blended.g;
      colors[i * 3 + 2] = blended.b;
    }
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const material = new THREE.MeshBasicMaterial({
      vertexColors: true,
      wireframe: false,
      transparent: true,
      opacity: 0.88,
      side: THREE.DoubleSide,
    });

    const waveMesh = new THREE.Mesh(geometry, material);
    waveMesh.rotation.x = -Math.PI / 4.2;
    waveMesh.position.y = -0.5;
    scene.add(waveMesh);

    // Ambient Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.9);
    scene.add(ambientLight);

    let animationId;
    let clock = new THREE.Clock();

    function animate() {
      animationId = requestAnimationFrame(animate);

      if (!prefersReducedMotion) {
        const time = clock.getElapsedTime() * 0.45;
        const positions = geometry.attributes.position.array;

        for (let i = 0; i < posAttr.count; i++) {
          const idx = i * 3;
          const origX = originalPositions[idx];
          const origY = originalPositions[idx + 1];

          // Harmonic Sinusoid Waveform
          const wave1 = Math.sin(origX * 0.55 + time * 1.2) * 0.38;
          const wave2 = Math.cos(origY * 0.65 - time * 0.9) * 0.28;
          const wave3 = Math.sin((origX + origY) * 0.35 + time * 0.6) * 0.22;

          positions[idx + 2] = wave1 + wave2 + wave3;
        }

        geometry.attributes.position.needsUpdate = true;
        waveMesh.rotation.z = Math.sin(time * 0.15) * 0.04;
      }

      renderer.render(scene, camera);
    }

    animate();

    // Window Resize Handling
    function onResize() {
      if (!renderer || !camera) return;
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    }

    window.addEventListener('resize', onResize, { passive: true });

    // Cleanup on unload
    window.addEventListener('beforeunload', () => {
      cancelAnimationFrame(animationId);
      window.removeEventListener('resize', onResize);
      geometry.dispose();
      material.dispose();
      renderer.dispose();
    });
  }

  function init2DFallback(canvas) {
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let width = (canvas.width = window.innerWidth);
    let height = (canvas.height = window.innerHeight);

    let time = 0;
    function render2D() {
      time += 0.008;
      ctx.clearRect(0, 0, width, height);

      const grad = ctx.createRadialGradient(
        width * 0.4 + Math.sin(time) * 100,
        height * 0.3 + Math.cos(time * 0.8) * 80,
        50,
        width * 0.5,
        height * 0.5,
        width * 0.75
      );
      grad.addColorStop(0, 'rgba(124, 58, 237, 0.4)');
      grad.addColorStop(0.4, 'rgba(99, 102, 241, 0.25)');
      grad.addColorStop(0.7, 'rgba(168, 85, 247, 0.12)');
      grad.addColorStop(1, 'rgba(11, 8, 23, 0)');

      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, width, height);

      requestAnimationFrame(render2D);
    }

    render2D();

    window.addEventListener('resize', () => {
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
    });
  }

  // Self-mount when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAuroraWave);
  } else {
    initAuroraWave();
  }
})();
