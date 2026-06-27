/*
  TeslaCam Plate Dashboard – UI helpers
  - Mobile sidebar toggle
  - Global scanner status polling
  - "Scan now" button

  This file intentionally has zero external dependencies so the dashboard
  continues to work offline.
*/

(function () {
  // ---------------------------------------------------------------------------
  // Theme toggle (light/dark)
  // ---------------------------------------------------------------------------
  const themeToggle = document.getElementById('themeToggle');
  const root = document.documentElement;

  function applyTheme(theme) {
    const t = (theme === 'dark') ? 'dark' : 'light';
    root.setAttribute('data-theme', t);
    try { localStorage.setItem('alpr_theme', t); } catch (e) {}
  }

  try {
    const saved = localStorage.getItem('alpr_theme');
    if (saved) applyTheme(saved);
  } catch (e) {}

  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      const cur = root.getAttribute('data-theme') || 'light';
      applyTheme(cur === 'dark' ? 'light' : 'dark');
    });
  }

  const body = document.body;
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('navOverlay');
  const btnToggle = document.getElementById('navToggle');
  const btnClose = document.getElementById('navClose');

  function setNav(open) {
    if (!sidebar) return;
    body.classList.toggle('nav-open', !!open);
    if (btnToggle) btnToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (overlay) overlay.setAttribute('aria-hidden', open ? 'false' : 'true');
  }

  if (btnToggle) btnToggle.addEventListener('click', () => setNav(!body.classList.contains('nav-open')));
  if (btnClose) btnClose.addEventListener('click', () => setNav(false));
  if (overlay) overlay.addEventListener('click', () => setNav(false));
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') setNav(false);
  });

  // Scanner status polling (topbar + sidebar foot)
  const scanDot = document.getElementById('scanDot');
  const scanStatus = document.getElementById('scanStatus');
  const navScanDot = document.getElementById('navScanDot');
  const navScanStatus = document.getElementById('navScanStatus');

  function applyScanState(statusRaw) {
    const status = String(statusRaw || 'unknown').toLowerCase();
    const cls = status.includes('error') ? 'bad' : (status.includes('scan') || status.includes('run')) ? 'warn' : 'ok';
    if (scanDot) scanDot.className = `dot ${cls}`;
    if (navScanDot) navScanDot.className = `dot ${cls}`;
    if (scanStatus) scanStatus.textContent = status;
    if (navScanStatus) navScanStatus.textContent = status;
  }

  async function refreshState() {
    try {
      const resp = await fetch('/api/state', { cache: 'no-store' });
      const data = await resp.json();
      applyScanState(data.status);
    } catch (e) {
      applyScanState('offline');
    }
  }

  refreshState();
  setInterval(refreshState, 5000);

  // Scan-now button
  const scanNow = document.getElementById('scanNowBtn');
  if (scanNow) {
    scanNow.addEventListener('click', async () => {
      scanNow.disabled = true;
      const prev = scanNow.textContent;
      scanNow.textContent = 'Queued…';
      try {
        await fetch('/api/scan_now', { method: 'POST' });
      } catch (e) {
        // ignore
      }
      setTimeout(() => {
        scanNow.textContent = prev;
        scanNow.disabled = false;
      }, 1500);
    });
  }
})();
