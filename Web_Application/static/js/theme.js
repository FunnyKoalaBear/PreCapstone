// Theme: mode is auto, light or dark; data-theme on <html> holds the resolved value
(() => {
  const root = document.documentElement;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const read = () => {
    try { const v = localStorage.getItem('theme'); return v === 'light' || v === 'dark' ? v : 'auto'; } catch (e) { return 'auto'; }
  };
  let mode = read();
  let printing = false;
  const resolve = () => (mode === 'auto' ? (media.matches ? 'dark' : 'light') : mode);

  const apply = () => {
    root.dataset.theme = printing ? 'light' : resolve();
    document.dispatchEvent(new CustomEvent('themechange', { detail: { mode, theme: resolve() } }));
  };

  window.FT = window.FT || {};
  window.FT.getTheme = () => mode;
  window.FT.setTheme = (next) => {
    mode = next === 'light' || next === 'dark' ? next : 'auto';
    try { localStorage.setItem('theme', mode); } catch (e) { /* storage blocked: keep it for this page only */ }
    apply();
  };

  media.addEventListener('change', () => { if (mode === 'auto') apply(); });
  // Printing and PDFs always stay light
  window.addEventListener('beforeprint', () => { printing = true; apply(); });
  window.addEventListener('afterprint', () => { printing = false; apply(); });

  // Temporary buttons (U3 moves them into the account menu): cycle Auto, Light, Dark
  document.addEventListener('DOMContentLoaded', () => {
    const buttons = document.querySelectorAll('[data-theme-toggle]');
    const order = ['auto', 'light', 'dark'];
    const show = () => buttons.forEach((button) => {
      const name = button.dataset[mode];
      button.dataset.mode = mode;
      button.querySelector('[data-theme-text]').textContent = name;
      button.setAttribute('aria-label', `${button.dataset.label}: ${name}`);
      button.title = `${button.dataset.label}: ${name}`;
    });
    buttons.forEach((button) => button.addEventListener('click', () => window.FT.setTheme(order[(order.indexOf(mode) + 1) % 3])));
    document.addEventListener('themechange', show);
    show();
  });
})();
