// Sidebar: pinned or collapsed on desktop (saved), off-canvas drawer on small screens
document.addEventListener('DOMContentLoaded', () => {
  const root = document.documentElement;
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return;
  const toggle = sidebar.querySelector('.sidebar-toggle');
  const burger = document.querySelector('.hamburger');

  const showMode = () => {
    const pinned = root.dataset.sidebar === 'pinned';
    const label = pinned ? toggle.dataset.collapse : toggle.dataset.pin;
    toggle.setAttribute('aria-pressed', String(pinned));
    toggle.setAttribute('aria-label', label);
    toggle.title = label;
    toggle.querySelector('.nav-label').textContent = label;
  };
  toggle.addEventListener('click', () => {
    root.dataset.sidebar = root.dataset.sidebar === 'pinned' ? 'collapsed' : 'pinned';
    try { localStorage.setItem('sidebar', root.dataset.sidebar); } catch (e) { /* storage blocked */ }
    showMode();
  });
  showMode();

  const setDrawer = (open, refocus) => {
    root.classList.toggle('drawer-open', open);
    burger.setAttribute('aria-expanded', String(open));
    if (!open && refocus) burger.focus();
  };
  burger.addEventListener('click', () => setDrawer(!root.classList.contains('drawer-open')));
  document.querySelector('.sidebar-backdrop').addEventListener('click', () => setDrawer(false, true));
  sidebar.addEventListener('click', (e) => { if (e.target.closest('a')) setDrawer(false); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && root.classList.contains('drawer-open')) setDrawer(false, true);
  });
});
