/**
 * Smriti — Landing Page JavaScript
 * Particle canvas, scroll reveal, nav scroll effect, typing animation
 */

'use strict';

/* ─────────────────────────────────────────────────────────────────────────────
   1. Particle Canvas (Knowledge Graph Background)
   ───────────────────────────────────────────────────────────────────────────── */

class ParticleSystem {
  constructor(canvas) {
    this.canvas  = canvas;
    this.ctx     = canvas.getContext('2d');
    this.nodes   = [];
    this.mouse   = { x: -1000, y: -1000 };
    this.animId  = null;

    this._resize  = this.resize.bind(this);
    this._animate = this.animate.bind(this);
    this._onMouse = this.onMouse.bind(this);

    window.addEventListener('resize', this._resize);
    window.addEventListener('mousemove', this._onMouse);

    this.resize();
    this.animate();
  }

  resize() {
    this.canvas.width  = window.innerWidth;
    this.canvas.height = window.innerHeight;
    this.initNodes();
  }

  initNodes() {
    const count = Math.min(60, Math.floor((this.canvas.width * this.canvas.height) / 22000));
    this.nodes = Array.from({ length: count }, () => ({
      x:    Math.random() * this.canvas.width,
      y:    Math.random() * this.canvas.height,
      vx:   (Math.random() - 0.5) * 0.35,
      vy:   (Math.random() - 0.5) * 0.35,
      r:    Math.random() * 2 + 1,
      hue:  220 + Math.random() * 60,  // blue to violet range
      alpha: Math.random() * 0.5 + 0.2,
    }));
  }

  onMouse(e) {
    this.mouse.x = e.clientX;
    this.mouse.y = e.clientY;
  }

  animate() {
    const { ctx, canvas, nodes, mouse } = this;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const maxDist   = 140;
    const mouseDist = 200;

    // Update and draw nodes
    for (const node of nodes) {
      // Gentle mouse repulsion
      const dx = node.x - mouse.x;
      const dy = node.y - mouse.y;
      const d  = Math.sqrt(dx * dx + dy * dy);
      if (d < mouseDist && d > 0) {
        const force = (mouseDist - d) / mouseDist * 0.6;
        node.vx += (dx / d) * force;
        node.vy += (dy / d) * force;
      }

      // Velocity damping
      node.vx *= 0.98;
      node.vy *= 0.98;

      node.x += node.vx;
      node.y += node.vy;

      // Bounce off edges
      if (node.x < 0 || node.x > canvas.width)  node.vx *= -1;
      if (node.y < 0 || node.y > canvas.height)  node.vy *= -1;
      node.x = Math.max(0, Math.min(canvas.width,  node.x));
      node.y = Math.max(0, Math.min(canvas.height, node.y));

      // Draw node
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${node.hue}, 70%, 65%, ${node.alpha})`;
      ctx.fill();
    }

    // Draw edges between nearby nodes
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a  = nodes[i], b = nodes[j];
        const dx = a.x - b.x, dy = a.y - b.y;
        const d  = Math.sqrt(dx * dx + dy * dy);
        if (d < maxDist) {
          const opacity = (1 - d / maxDist) * 0.25;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.strokeStyle = `rgba(120, 130, 240, ${opacity})`;
          ctx.lineWidth   = 0.8;
          ctx.stroke();
        }
      }
    }

    this.animId = requestAnimationFrame(this._animate);
  }

  destroy() {
    cancelAnimationFrame(this.animId);
    window.removeEventListener('resize', this._resize);
    window.removeEventListener('mousemove', this._onMouse);
  }
}

/* ─────────────────────────────────────────────────────────────────────────────
   2. Scroll Reveal (Intersection Observer)
   ───────────────────────────────────────────────────────────────────────────── */

function initScrollReveal() {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.12, rootMargin: '0px 0px -40px 0px' }
  );

  document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
}

/* ─────────────────────────────────────────────────────────────────────────────
   3. Navigation scroll effect
   ───────────────────────────────────────────────────────────────────────────── */

function initNav() {
  const nav = document.getElementById('nav');
  if (!nav) return;
  window.addEventListener('scroll', () => {
    nav.classList.toggle('scrolled', window.scrollY > 40);
  }, { passive: true });
}

/* ─────────────────────────────────────────────────────────────────────────────
   4. Hero typing animation
   ───────────────────────────────────────────────────────────────────────────── */

function initTypingAnimation() {
  const target = document.querySelector('.demo-query-text');
  if (!target) return;

  const queries = [
    '"What\'s our deployment process for the payments service?"',
    '"Who should I talk to about the authentication system?"',
    '"Why did we choose Postgres over MySQL?"',
    '"What were the Q1 requirements for the billing feature?"',
    '"Who are the top contributors to the infrastructure codebase?"',
  ];

  let qIdx   = 0;
  let cIdx   = 0;
  let typing = true;

  const type = () => {
    const current = queries[qIdx];
    if (typing) {
      if (cIdx < current.length) {
        target.textContent = current.slice(0, ++cIdx);
        setTimeout(type, 35 + Math.random() * 25);
      } else {
        typing = false;
        setTimeout(type, 2800);
      }
    } else {
      if (cIdx > 0) {
        target.textContent = current.slice(0, --cIdx);
        setTimeout(type, 18);
      } else {
        typing = true;
        qIdx   = (qIdx + 1) % queries.length;
        setTimeout(type, 400);
      }
    }
  };

  // Start after a short delay so page is settled
  setTimeout(type, 1200);
}

/* ─────────────────────────────────────────────────────────────────────────────
   5. Smooth anchor scroll
   ───────────────────────────────────────────────────────────────────────────── */

function initSmoothScroll() {
  document.querySelectorAll('a[href^="#"]').forEach(link => {
    link.addEventListener('click', (e) => {
      const id = link.getAttribute('href').slice(1);
      const el = document.getElementById(id);
      if (el) {
        e.preventDefault();
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });
}

/* ─────────────────────────────────────────────────────────────────────────────
   6. Number counter animation for social proof stats
   ───────────────────────────────────────────────────────────────────────────── */

function initCounters() {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const el   = entry.target;
        const text = el.textContent.trim();

        // Only animate pure numbers
        const num = parseFloat(text.replace(/[^0-9.]/g, ''));
        if (isNaN(num) || text.includes('<') || text.includes('%') === false && !text.includes('ms')) return;

        let start   = 0;
        const dur   = 1600;
        const tick  = 16;
        const steps = dur / tick;
        const inc   = num / steps;

        const update = () => {
          start = Math.min(start + inc, num);
          if (text.includes('%'))      el.textContent = Math.round(start) + '%';
          else if (text.includes('ms')) el.textContent = '<' + Math.round(start) + 'ms';
          if (start < num) setTimeout(update, tick);
        };
        update();
        observer.unobserve(el);
      });
    },
    { threshold: 0.8 }
  );

  document.querySelectorAll('.sp-num').forEach(el => observer.observe(el));
}

/* ─────────────────────────────────────────────────────────────────────────────
   Init
   ───────────────────────────────────────────────────────────────────────────── */

/* ─────────────────────────────────────────────────────────────────────────────
   7. Theme Toggle
   ───────────────────────────────────────────────────────────────────────────── */

function initThemeToggle() {
  const btn  = document.getElementById('theme-toggle');
  if (!btn) return;

  const html = document.documentElement;
  const KEY  = 'smriti-theme';

  function applyTheme(t) {
    html.setAttribute('data-theme', t);
    localStorage.setItem(KEY, t);
  }

  // Apply persisted theme on load (also set in <head> inline script to avoid FOUC)
  const saved = localStorage.getItem(KEY) || 'dark';
  applyTheme(saved);

  btn.addEventListener('click', () => {
    const current = html.getAttribute('data-theme') || 'dark';
    applyTheme(current === 'dark' ? 'light' : 'dark');
  });
}

/* ─────────────────────────────────────────────────────────────────────────────
   Init
   ───────────────────────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
  // Particle canvas (behind everything)
  const canvas = document.getElementById('particle-canvas');
  if (canvas) new ParticleSystem(canvas);

  initScrollReveal();
  initNav();
  initTypingAnimation();
  initSmoothScroll();
  initCounters();
  initThemeToggle();

  // Mark all hero elements visible immediately (they are above fold)
  document.querySelectorAll('.hero .reveal').forEach(el => {
    setTimeout(() => el.classList.add('visible'), 100);
  });
});
