// Sidebar: open or collapsed on desktop (saved), off-canvas drawer on small screens
document.addEventListener('DOMContentLoaded', () => {
  const root = document.documentElement;
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return;
  const collapseButton = sidebar.querySelector('.sidebar-collapse');
  const logoButton = sidebar.querySelector('.sidebar-logo');
  const burger = document.querySelector('.hamburger');
  const desktop = window.matchMedia('(min-width: 48.0625em)');
  const isRail = () => desktop.matches && root.dataset.sidebar === 'collapsed';

  // One shared tooltip for the collapsed rail
  const tip = document.createElement('div');
  tip.className = 'rail-tip';
  tip.setAttribute('role', 'tooltip');
  tip.hidden = true;
  document.body.appendChild(tip);
  const targets = '.nav-item, .account-chip';
  const hideTip = () => { tip.hidden = true; };
  const showTip = (el) => {
    const text = el.dataset.tip || el.getAttribute('aria-label') || el.dataset.title;
    if (!isRail() || !text) return hideTip();
    const box = el.getBoundingClientRect();
    tip.textContent = text;
    tip.style.left = `${box.right + 8}px`;
    tip.style.top = `${box.top + box.height / 2}px`;
    tip.hidden = false;
  };
  // The native title would double the tooltip, so it is set aside while collapsed
  const syncTitles = () => {
    sidebar.querySelectorAll(targets).forEach((el) => {
      if (isRail() && el.title) { el.dataset.title = el.title; el.removeAttribute('title'); }
      else if (!isRail() && el.dataset.title) { el.title = el.dataset.title; delete el.dataset.title; }
    });
    hideTip();
  };

  const setMode = (mode) => {
    root.dataset.sidebar = mode;
    try { localStorage.setItem('sidebar', mode); } catch (e) { /* storage blocked */ }
    syncTitles();
    (mode === 'pinned' ? collapseButton : logoButton).focus();
  };
  collapseButton.addEventListener('click', () => setMode('collapsed'));
  logoButton.addEventListener('click', () => setMode('pinned'));
  // In the collapsed rail any click outside an icon opens it
  sidebar.addEventListener('click', (e) => {
    if (isRail() && !e.target.closest('a, button, .account-menu')) setMode('pinned');
  });
  syncTitles();
  desktop.addEventListener('change', syncTitles);

  sidebar.addEventListener('mouseover', (e) => {
    const el = e.target.closest(targets);
    if (el) showTip(el); else hideTip();
  });
  sidebar.addEventListener('mouseleave', hideTip);
  sidebar.addEventListener('focusin', (e) => { const el = e.target.closest(targets); if (el) showTip(el); });
  sidebar.addEventListener('focusout', hideTip);
  sidebar.addEventListener('click', (e) => { if (e.target.closest('.account-chip')) hideTip(); });
  sidebar.addEventListener('scroll', hideTip, true);
  window.addEventListener('scroll', hideTip, true);
  window.addEventListener('resize', hideTip);

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
