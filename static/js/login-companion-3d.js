/**
 * Rent2Swap - Interactive 3D Login/Register Mascot Companion
 * Based on Google Stitch Integrated 3D Companion Export
 * 
 * Interactive Three.js character running alongside Login and Register forms:
 * - Damped head & eye tracking following user's cursor.
 * - Interactive response to password field focus (shyly looks away/covers eyes).
 * - Celebratory head-nod and bounce on click/submit.
 * - Pure Vanilla JavaScript + Three.js, lightweight and battery-optimized.
 */

(function () {
  'use strict';

  function initLoginCompanion() {
    const container = document.getElementById('login-companion-container');
    const canvas = document.getElementById('companion-3d-canvas');
    if (!container || !canvas) {
      return;
    }

    if (typeof THREE === 'undefined') {
      console.warn('[Rent2Swap Companion] Three.js not loaded, companion inactive.');
      return;
    }

    try {
      setupCompanion(container, canvas);
    } catch (err) {
      console.warn('[Rent2Swap Companion] Failed to initialize 3D companion:', err);
    }
  }

  function setupCompanion(container, canvas) {
    const width = container.clientWidth || 320;
    const height = container.clientHeight || 380;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(40, width / height, 0.1, 100);
    camera.position.set(0, 0.3, 4.2);

    const renderer = new THREE.WebGLRenderer({
      canvas: canvas,
      alpha: true,
      antialias: true,
      powerPreference: 'high-performance',
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setSize(width, height);

    // Studio Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
    scene.add(ambientLight);

    const purpleLight = new THREE.PointLight(0xa855f7, 2.4, 12);
    purpleLight.position.set(-2, 2.5, 3);
    scene.add(purpleLight);

    const cyanLight = new THREE.PointLight(0x06b6d4, 1.8, 10);
    cyanLight.position.set(2.5, -1.5, 2);
    scene.add(cyanLight);

    const rimLight = new THREE.DirectionalLight(0xc084fc, 1.2);
    rimLight.position.set(0, 4, -2);
    scene.add(rimLight);

    // Character Group Root
    const character = new THREE.Group();
    scene.add(character);

    // 1. Robotic Head Base
    const headGeom = new THREE.SphereGeometry(0.72, 32, 32);
    headGeom.scale(1, 0.95, 0.9);
    const headMat = new THREE.MeshStandardMaterial({
      color: 0x1e153a,
      metalness: 0.65,
      roughness: 0.25,
    });
    const head = new THREE.Mesh(headGeom, headMat);
    character.add(head);

    // 2. Visor Screen
    const visorGeom = new THREE.SphereGeometry(0.56, 32, 24, 0, Math.PI * 2, 0, Math.PI / 1.7);
    visorGeom.scale(1, 0.72, 0.58);
    visorGeom.rotateX(Math.PI / 1.9);
    const visorMat = new THREE.MeshPhysicalMaterial({
      color: 0x080514,
      metalness: 0.85,
      roughness: 0.1,
      transmission: 0.3,
      thickness: 0.4,
      clearcoat: 1.0,
      clearcoatRoughness: 0.1,
    });
    const visor = new THREE.Mesh(visorGeom, visorMat);
    visor.position.set(0, 0.05, 0.46);
    head.add(visor);

    // 3. Glowing Digital Eyes
    const eyeGeom = new THREE.CapsuleGeometry
      ? new THREE.CapsuleGeometry(0.065, 0.14, 8, 16)
      : new THREE.CylinderGeometry(0.065, 0.065, 0.22, 16);

    const eyeMat = new THREE.MeshBasicMaterial({
      color: 0x38bdf8,
    });

    const leftEye = new THREE.Mesh(eyeGeom, eyeMat);
    leftEye.position.set(-0.24, 0.06, 0.68);
    leftEye.rotation.z = Math.PI / 14;
    head.add(leftEye);

    const rightEye = new THREE.Mesh(eyeGeom, eyeMat);
    rightEye.position.set(0.24, 0.06, 0.68);
    rightEye.rotation.z = -Math.PI / 14;
    head.add(rightEye);

    // Eye glow halo
    const eyeHaloMat = new THREE.MeshBasicMaterial({
      color: 0xa855f7,
      transparent: true,
      opacity: 0.35,
    });
    const leftHalo = new THREE.Mesh(eyeGeom, eyeHaloMat);
    leftHalo.scale.set(1.4, 1.4, 1.4);
    leftEye.add(leftHalo);

    const rightHalo = new THREE.Mesh(eyeGeom, eyeHaloMat);
    rightHalo.scale.set(1.4, 1.4, 1.4);
    rightEye.add(rightHalo);

    // 4. Floating Magnetic Ear Cups
    const earGeom = new THREE.CylinderGeometry(0.18, 0.18, 0.1, 24);
    earGeom.rotateZ(Math.PI / 2);
    const earMat = new THREE.MeshStandardMaterial({
      color: 0x7c3aed,
      metalness: 0.8,
      roughness: 0.2,
    });

    const leftEar = new THREE.Mesh(earGeom, earMat);
    leftEar.position.set(-0.82, 0.08, 0);
    head.add(leftEar);

    const rightEar = new THREE.Mesh(earGeom, earMat);
    rightEar.position.set(0.82, 0.08, 0);
    head.add(rightEar);

    // 5. Floating Torso Orb & Neck Ring
    const neckRingGeom = new THREE.TorusGeometry(0.42, 0.05, 16, 32);
    neckRingGeom.rotateX(Math.PI / 2);
    const ringMat = new THREE.MeshStandardMaterial({
      color: 0xa855f7,
      metalness: 0.9,
      roughness: 0.15,
      emissive: 0x4c1d95,
      emissiveIntensity: 0.4,
    });
    const neckRing = new THREE.Mesh(neckRingGeom, ringMat);
    neckRing.position.set(0, -0.85, 0);
    character.add(neckRing);

    const bodyGeom = new THREE.SphereGeometry(0.48, 24, 24);
    bodyGeom.scale(1, 0.9, 0.8);
    const bodyMat = new THREE.MeshStandardMaterial({
      color: 0x160f2e,
      metalness: 0.5,
      roughness: 0.3,
    });
    const body = new THREE.Mesh(bodyGeom, bodyMat);
    body.position.set(0, -1.35, -0.05);
    character.add(body);

    // Small chest badge
    const badgeGeom = new THREE.CylinderGeometry(0.1, 0.1, 0.04, 16);
    badgeGeom.rotateX(Math.PI / 2);
    const badgeMat = new THREE.MeshBasicMaterial({ color: 0x10b981 });
    const badge = new THREE.Mesh(badgeGeom, badgeMat);
    badge.position.set(0, -1.25, 0.38);
    character.add(badge);

    // Interaction Variables
    let targetRotationX = 0;
    let targetRotationY = 0;
    let isHidingPassword = false;
    let nodOffset = 0;
    let isBlinking = false;

    // Track mouse on window
    window.addEventListener('mousemove', (e) => {
      if (isHidingPassword) return;

      const rect = canvas.getBoundingClientRect();
      const canvasCenterX = rect.left + rect.width / 2;
      const canvasCenterY = rect.top + rect.height / 2;

      const normX = (e.clientX - canvasCenterX) / (window.innerWidth / 2);
      const normY = (e.clientY - canvasCenterY) / (window.innerHeight / 2);

      targetRotationY = Math.max(-0.6, Math.min(0.6, normX * 0.7));
      targetRotationX = Math.max(-0.4, Math.min(0.4, normY * 0.45));
    }, { passive: true });

    // Password Focus Handling: Companion looks down politely and dims eyes
    const passwordInputs = document.querySelectorAll('input[type="password"]');
    passwordInputs.forEach((input) => {
      input.addEventListener('focus', () => {
        isHidingPassword = true;
        // Companion lowers head and looks away
        targetRotationX = 0.55; // Tilt head down
        targetRotationY = 0.35; // Slight angle
        eyeMat.color.setHex(0xa855f7); // Soft shy violet
        leftEye.scale.set(0.7, 0.2, 0.7); // Crescent closed eye
        rightEye.scale.set(0.7, 0.2, 0.7);
      });

      input.addEventListener('blur', () => {
        isHidingPassword = false;
        eyeMat.color.setHex(0x38bdf8); // Return to bright cyan
        leftEye.scale.set(1, 1, 1);
        rightEye.scale.set(1, 1, 1);
      });
    });

    // Friendly click / submit nod
    const submitBtn = document.querySelector('button[type="submit"]');
    if (submitBtn) {
      submitBtn.addEventListener('mouseenter', () => {
        nodOffset = -0.15; // Nod forward
      });
      submitBtn.addEventListener('mouseleave', () => {
        nodOffset = 0;
      });
    }

    canvas.addEventListener('click', () => {
      // Cheerful double bounce
      nodOffset = -0.3;
      setTimeout(() => (nodOffset = 0.15), 180);
      setTimeout(() => (nodOffset = 0), 360);
    });

    // Automatic Natural Blinking Loop
    setInterval(() => {
      if (isHidingPassword) return;
      isBlinking = true;
      leftEye.scale.y = 0.1;
      rightEye.scale.y = 0.1;

      setTimeout(() => {
        leftEye.scale.y = 1;
        rightEye.scale.y = 1;
        isBlinking = false;
      }, 140);
    }, 4200);

    // Animation Render Loop
    let clock = new THREE.Clock();
    let animId;

    function animate() {
      animId = requestAnimationFrame(animate);
      const elapsed = clock.getElapsedTime();

      // Idle Hover & Bobbing
      character.position.y = Math.sin(elapsed * 2.2) * 0.07;
      neckRing.rotation.y = elapsed * 0.8;

      // Smooth Head Tracking Interpolation (Lerp)
      head.rotation.y += (targetRotationY - head.rotation.y) * 0.08;
      head.rotation.x += (targetRotationX + nodOffset - head.rotation.x) * 0.08;
      head.rotation.z = Math.sin(elapsed * 1.5) * 0.02 - (head.rotation.y * 0.1);

      // Body subtle follow
      body.rotation.y += (targetRotationY * 0.4 - body.rotation.y) * 0.05;

      renderer.render(scene, camera);
    }

    animate();

    // Container Resize Listener
    function onResize() {
      const newWidth = container.clientWidth || 320;
      const newHeight = container.clientHeight || 380;
      camera.aspect = newWidth / newHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(newWidth, newHeight);
    }

    window.addEventListener('resize', onResize, { passive: true });

    window.addEventListener('beforeunload', () => {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', onResize);
      headGeom.dispose();
      headMat.dispose();
      visorGeom.dispose();
      visorMat.dispose();
      renderer.dispose();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initLoginCompanion);
  } else {
    initLoginCompanion();
  }
})();
