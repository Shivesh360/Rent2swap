/**
 * Rent2Swap - Hero 3D Structure & Interactive Visualization
 * Based on Google Stitch Three.js Export (three.js_1 / three.js_2)
 *
 * Renders the 3D holographic structure in the reserved right-side hero area:
 * - Floating crystalline geometry with glowing orbital rings and particles
 * - Responsive to mouse parallax / cursor motion
 * - Violet (#a855f7), Indigo (#6366f1) and Cyan (#06b6d4) studio lighting
 * - Battery-optimized: uses requestAnimationFrame with visibility detection
 */

(function () {
  'use strict';

  function initHero3D() {
    const container = document.getElementById('hero-3d-container');
    const canvas = document.getElementById('hero-3d-canvas');
    if (!container || !canvas) return;

    if (typeof THREE === 'undefined') {
      console.warn('[Rent2Swap 3D] Three.js not loaded, 3D structure skipped.');
      return;
    }

    try {
      setupHeroScene(container, canvas);
    } catch (err) {
      console.warn('[Rent2Swap 3D] Failed to initialize hero 3D scene:', err);
    }
  }

  function setupHeroScene(container, canvas) {
    const width = container.clientWidth || 400;
    const height = container.clientHeight || 380;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
    camera.position.set(0, 0, 4.6);

    const renderer = new THREE.WebGLRenderer({
      canvas: canvas,
      alpha: true,
      antialias: true,
      powerPreference: 'high-performance'
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setSize(width, height);

    // Studio Lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.85);
    scene.add(ambientLight);

    const violetLight = new THREE.PointLight(0xa855f7, 3.2, 14);
    violetLight.position.set(2.5, 3, 2.5);
    scene.add(violetLight);

    const cyanLight = new THREE.PointLight(0x06b6d4, 2.4, 12);
    cyanLight.position.set(-3, -2, 2);
    scene.add(cyanLight);

    const rimLight = new THREE.DirectionalLight(0xc084fc, 1.5);
    rimLight.position.set(0, 4, -2);
    scene.add(rimLight);

    // Root Group
    const rootGroup = new THREE.Group();
    scene.add(rootGroup);

    // 1. Central Holographic Torus Knot
    const knotGeom = new THREE.TorusKnotGeometry(0.95, 0.28, 128, 24, 2, 3);
    const knotMat = new THREE.MeshPhysicalMaterial({
      color: 0x4c1d95,
      emissive: 0x2e1065,
      emissiveIntensity: 0.45,
      roughness: 0.15,
      metalness: 0.85,
      clearcoat: 1.0,
      clearcoatRoughness: 0.1,
      wireframe: false
    });
    const knotMesh = new THREE.Mesh(knotGeom, knotMat);
    rootGroup.add(knotMesh);

    // Inner wireframe overlay for tech/futuristic feel
    const wireGeom = new THREE.TorusKnotGeometry(0.97, 0.27, 64, 16, 2, 3);
    const wireMat = new THREE.MeshBasicMaterial({
      color: 0xc084fc,
      wireframe: true,
      transparent: true,
      opacity: 0.18
    });
    const wireMesh = new THREE.Mesh(wireGeom, wireMat);
    rootGroup.add(wireMesh);

    // 2. Orbital Ring 1
    const ring1Geom = new THREE.TorusGeometry(1.65, 0.02, 16, 96);
    const ring1Mat = new THREE.MeshBasicMaterial({
      color: 0xa855f7,
      transparent: true,
      opacity: 0.6
    });
    const ring1 = new THREE.Mesh(ring1Geom, ring1Mat);
    ring1.rotation.x = Math.PI / 3;
    rootGroup.add(ring1);

    // 3. Orbital Ring 2 (Perpendicular)
    const ring2Geom = new THREE.TorusGeometry(1.85, 0.015, 16, 96);
    const ring2Mat = new THREE.MeshBasicMaterial({
      color: 0x06b6d4,
      transparent: true,
      opacity: 0.5
    });
    const ring2 = new THREE.Mesh(ring2Geom, ring2Mat);
    ring2.rotation.y = Math.PI / 4;
    ring2.rotation.x = Math.PI / 6;
    rootGroup.add(ring2);

    // 4. Floating Particles Cloud
    const particleCount = 70;
    const partGeom = new THREE.BufferGeometry();
    const positions = new Float32Array(particleCount * 3);
    const colors = new Float32Array(particleCount * 3);
    const colA = new THREE.Color(0xa855f7);
    const colB = new THREE.Color(0x06b6d4);

    for (let i = 0; i < particleCount; i++) {
      const radius = 1.3 + Math.random() * 1.2;
      const theta = Math.random() * Math.PI * 2;
      const phi = (Math.random() - 0.5) * Math.PI;

      positions[i * 3] = radius * Math.cos(phi) * Math.cos(theta);
      positions[i * 3 + 1] = radius * Math.sin(phi);
      positions[i * 3 + 2] = radius * Math.cos(phi) * Math.sin(theta);

      const mixedCol = colA.clone().lerp(colB, Math.random());
      colors[i * 3] = mixedCol.r;
      colors[i * 3 + 1] = mixedCol.g;
      colors[i * 3 + 2] = mixedCol.b;
    }
    partGeom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    partGeom.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const partMat = new THREE.PointsMaterial({
      size: 0.055,
      vertexColors: true,
      transparent: true,
      opacity: 0.85
    });
    const particles = new THREE.Points(partGeom, partMat);
    rootGroup.add(particles);

    // Parallax Interaction
    let targetRotX = 0;
    let targetRotY = 0;
    let currentRotX = 0;
    let currentRotY = 0;

    window.addEventListener('mousemove', function (e) {
      const normX = (e.clientX / window.innerWidth) * 2 - 1;
      const normY = -(e.clientY / window.innerHeight) * 2 + 1;
      targetRotY = normX * 0.45;
      targetRotX = -normY * 0.35;
    }, { passive: true });

    // Handle Window Resize
    function onResize() {
      if (!container || !renderer || !camera) return;
      const newW = container.clientWidth || 400;
      const newH = container.clientHeight || 380;
      camera.aspect = newW / newH;
      camera.updateProjectionMatrix();
      renderer.setSize(newW, newH);
    }
    window.addEventListener('resize', onResize, { passive: true });

    // Animation Loop
    let isVisible = true;
    const observer = new IntersectionObserver((entries) => {
      isVisible = entries[0].isIntersecting;
    });
    observer.observe(container);

    let clock = new THREE.Clock();

    function animate() {
      requestAnimationFrame(animate);
      if (!isVisible) return;

      const delta = clock.getDelta();
      const time = clock.getElapsedTime();

      // Smooth damped rotation towards cursor
      currentRotX += (targetRotX - currentRotX) * 0.05;
      currentRotY += (targetRotY - currentRotY) * 0.05;

      rootGroup.rotation.x = currentRotX + Math.sin(time * 0.5) * 0.08;
      rootGroup.rotation.y = currentRotY + time * 0.28;

      // Independent ring rotations
      ring1.rotation.z += 0.008;
      ring2.rotation.z -= 0.01;
      particles.rotation.y -= 0.004;

      // Subtle vertical bob
      rootGroup.position.y = Math.sin(time * 1.2) * 0.08;

      renderer.render(scene, camera);
    }

    animate();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initHero3D);
  } else {
    initHero3D();
  }
})();
