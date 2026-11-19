/**
 * Rent2Swap - Organic Floating Thread Effect
 * 
 * STRICT VISUAL DISCIPLINE:
 * - NOT a network, NOT a node graph, NO spider-webs, NO blockchain grid.
 * - Only 4 to 5 ultra-thin, low-opacity, smooth organic floating curved strands.
 * - Leaves vast areas of empty space.
 * - Cursor proximity gently bends/deforms strands with subtle purple luminescence.
 * - Click creates a tiny, soft localized ripple pulse.
 * - Page-Specific: Only runs if #r2s-threads-canvas exists (Home and Auth screens only).
 */

(function () {
  'use strict';

  function initFloatingThreads() {
    const canvas = document.getElementById('r2s-threads-canvas');
    if (!canvas) {
      // Intentionally do nothing if not on allowed pages
      return;
    }

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let width = (canvas.width = window.innerWidth);
    let height = (canvas.height = window.innerHeight);

    // Mouse & Click Interaction State
    const mouse = {
      x: -9999,
      y: -9999,
      active: false,
      radius: 140, // Proximity radius for deformation
    };

    // Soft click ripples
    const ripples = [];

    window.addEventListener('mousemove', (e) => {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
      mouse.active = true;
    }, { passive: true });

    window.addEventListener('mouseleave', () => {
      mouse.active = false;
      mouse.x = -9999;
      mouse.y = -9999;
    });

    window.addEventListener('click', (e) => {
      ripples.push({
        x: e.clientX,
        y: e.clientY,
        radius: 0,
        maxRadius: 180,
        strength: 22,
        life: 1.0,
      });
    });

    // 4 to 5 Ultra-Thin Strands (No dense lines, vast empty spaces)
    const strandCount = 5;
    const strands = [];

    for (let s = 0; s < strandCount; s++) {
      const pointCount = 6;
      const points = [];
      
      // Distribute starting points diagonally or horizontally across canvas
      const startY = (height * 0.18) + (s * (height * 0.16)) + (Math.random() * 40 - 20);
      const startX = -60;
      const endX = width + 60;
      const stepX = (endX - startX) / (pointCount - 1);

      for (let p = 0; p < pointCount; p++) {
        const baseX = startX + p * stepX;
        const baseY = startY + Math.sin(p * 0.8 + s) * 45;
        points.push({
          baseX: baseX,
          baseY: baseY,
          x: baseX,
          y: baseY,
          vx: 0,
          vy: 0,
          phase: Math.random() * Math.PI * 2,
          speed: 0.0008 + Math.random() * 0.0006, // extremely slow organic float
          ampX: 18 + Math.random() * 22,
          ampY: 28 + Math.random() * 32,
        });
      }

      strands.push({
        points: points,
        colorBase: s % 2 === 0 ? 'rgba(168, 85, 247,' : 'rgba(192, 132, 252,', // Purple / Violet
        alpha: 0.14 + (s * 0.025), // 0.14 - 0.24 opacity (barely visible)
        width: 1.1, // Ultra-thin
        glowAlpha: 0.2,
      });
    }

    let time = 0;
    let animId;

    function render() {
      animId = requestAnimationFrame(render);
      time += 1;

      ctx.clearRect(0, 0, width, height);

      // Update ripples
      for (let r = ripples.length - 1; r >= 0; r--) {
        const rip = ripples[r];
        rip.radius += 3.5;
        rip.life = 1.0 - (rip.radius / rip.maxRadius);
        if (rip.life <= 0) {
          ripples.splice(r, 1);
        }
      }

      // Draw each strand as a smooth curved spline
      for (let s = 0; s < strands.length; s++) {
        const strand = strands[s];
        const pts = strand.points;

        // 1. Update organic float positions & handle physics
        let maxProximityGlow = 0;

        for (let i = 0; i < pts.length; i++) {
          const pt = pts[i];

          // Natural resting harmonic oscillation
          const targetX = pt.baseX + Math.sin(time * pt.speed + pt.phase) * pt.ampX;
          const targetY = pt.baseY + Math.cos(time * pt.speed * 0.9 + pt.phase) * pt.ampY;

          // Mouse proximity deformation
          let dx = pt.x - mouse.x;
          let dy = pt.y - mouse.y;
          let dist = Math.sqrt(dx * dx + dy * dy);

          let pushX = 0;
          let pushY = 0;

          if (mouse.active && dist < mouse.radius && dist > 1) {
            const force = (1 - dist / mouse.radius);
            // Gentle organic bend away from cursor
            const angle = Math.atan2(dy, dx);
            pushX = Math.cos(angle) * force * 40;
            pushY = Math.sin(angle) * force * 40;

            if (force > maxProximityGlow) {
              maxProximityGlow = force;
            }
          }

          // Soft Click Ripple Influence
          for (let r = 0; r < ripples.length; r++) {
            const rip = ripples[r];
            const rdx = pt.x - rip.x;
            const rdy = pt.y - rip.y;
            const rdist = Math.sqrt(rdx * rdx + rdy * rdy);
            const diff = Math.abs(rdist - rip.radius);
            if (diff < 40) {
              const ripForce = (1 - diff / 40) * rip.life * rip.strength;
              const angle = Math.atan2(rdy, rdx);
              pushX += Math.cos(angle) * ripForce;
              pushY += Math.sin(angle) * ripForce;
            }
          }

          // Smooth spring physics back toward target + push
          const destX = targetX + pushX;
          const destY = targetY + pushY;

          pt.vx = (pt.vx + (destX - pt.x) * 0.04) * 0.88;
          pt.vy = (pt.vy + (destY - pt.y) * 0.04) * 0.88;

          pt.x += pt.vx;
          pt.y += pt.vy;
        }

        // 2. Render smooth Catmull-Rom or Bezier curve
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);

        for (let i = 0; i < pts.length - 1; i++) {
          const current = pts[i];
          const next = pts[i + 1];
          // Midpoint quadratic control curve
          const midX = (current.x + next.x) / 2;
          const midY = (current.y + next.y) / 2;
          ctx.quadraticCurveTo(current.x, current.y, midX, midY);
        }
        // Final segment
        const last = pts[pts.length - 1];
        ctx.lineTo(last.x, last.y);

        // Subtle glow modulation when cursor is near
        const currentAlpha = strand.alpha + (maxProximityGlow * 0.28);
        ctx.strokeStyle = strand.colorBase + currentAlpha + ')';
        ctx.lineWidth = strand.width + (maxProximityGlow * 0.6);

        if (maxProximityGlow > 0.05) {
          ctx.shadowColor = 'rgba(192, 132, 252, 0.4)';
          ctx.shadowBlur = 12 * maxProximityGlow;
        } else {
          ctx.shadowColor = 'transparent';
          ctx.shadowBlur = 0;
        }

        ctx.stroke();
      }
    }

    render();

    function onResize() {
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
    }

    window.addEventListener('resize', onResize, { passive: true });

    window.addEventListener('beforeunload', () => {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', onResize);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initFloatingThreads);
  } else {
    initFloatingThreads();
  }
})();
