// Account menu: opens above the chip, arrow keys move between items, Escape or an outside click closes it
document.addEventListener('DOMContentLoaded', () => {
  const button = document.getElementById('account-button');
  const menu = document.getElementById('account-menu');
  if (!button || !menu) return;
  const sidebar = document.getElementById('sidebar');
  const items = () => [...menu.querySelectorAll('a[role="menuitem"], button')];

  const open = () => {
    const box = button.getBoundingClientRect();
    menu.style.left = `${Math.max(8, box.left)}px`;
    menu.style.bottom = `${window.innerHeight - box.top + 6}px`;
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    items()[0].focus();
  };
  const close = (refocus) => {
    if (menu.hidden) return;
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    if (refocus) button.focus();
  };

  button.addEventListener('click', () => (menu.hidden ? open() : close(true)));
  button.addEventListener('keydown', (e) => {
    if (menu.hidden && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) { e.preventDefault(); open(); }
  });

  menu.addEventListener('keydown', (e) => {
    const list = items();
    const at = list.indexOf(document.activeElement);
    if (e.key === 'Escape') { e.stopPropagation(); close(true); return; }
    const next = { ArrowDown: (at + 1) % list.length, ArrowUp: (at - 1 + list.length) % list.length, Home: 0, End: list.length - 1 }[e.key];
    if (next !== undefined) { e.preventDefault(); list[next].focus(); }
  });
  menu.addEventListener('focusout', (e) => {
    if (e.relatedTarget && !menu.contains(e.relatedTarget) && e.relatedTarget !== button) close(false);
  });
  document.addEventListener('click', (e) => {
    if (!menu.contains(e.target) && !button.contains(e.target)) close(false);
  });

  // The menu closes when the rail collapses or the sidebar mode changes
  const toggle = sidebar && sidebar.querySelector('.sidebar-toggle');
  if (toggle) toggle.addEventListener('click', () => close(false));
  if (sidebar) {
    sidebar.addEventListener('transitionend', (e) => {
      if (e.propertyName === 'width' && document.documentElement.dataset.sidebar === 'collapsed' && sidebar.offsetWidth < 100) close(false);
    });
  }
  window.addEventListener('resize', () => close(false));
});
