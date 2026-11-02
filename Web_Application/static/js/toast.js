// Toasts: the server renders each message as a list item; this adds close buttons, timers and a limit of three
(() => {
  const region = document.getElementById('toasts');
  if (!region) return;
  const list = region.querySelector('ul');
  const LIMIT = 3;
  const seconds = { success: 6, info: 6, debug: 6, warning: 10, error: 0 }; // 0 = stays until closed

  const dismiss = (toast) => { clearTimeout(toast._timer); toast.remove(); };

  const arm = (toast) => {
    const ms = (seconds[toast.dataset.level] ?? 6) * 1000;
    if (!ms) return;
    toast._left = toast._left ?? ms;
    toast._since = Date.now();
    toast._timer = setTimeout(() => dismiss(toast), toast._left);
  };
  const pause = (toast) => {
    if (!toast._since) return;
    clearTimeout(toast._timer);
    toast._left -= Date.now() - toast._since;
    toast._since = 0;
  };

  const setup = (toast) => {
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'toast-close';
    close.setAttribute('aria-label', region.dataset.close);
    close.textContent = '×';
    close.addEventListener('click', () => dismiss(toast));
    toast.append(close);
    toast.addEventListener('mouseenter', () => pause(toast));
    toast.addEventListener('focusin', () => pause(toast));
    toast.addEventListener('mouseleave', () => arm(toast));
    toast.addEventListener('focusout', () => arm(toast));
    arm(toast);
  };

  const limit = () => {
    const all = [...list.children];
    all.slice(0, Math.max(0, all.length - LIMIT)).forEach(dismiss);
  };

  list.querySelectorAll('li').forEach(setup);
  limit();

  // Used by the style guide and scripts: FT.toast('success', 'Saved.')
  window.FT = window.FT || {};
  window.FT.toast = (level, text) => {
    const toast = document.createElement('li');
    toast.className = `message toast toast-${level}`;
    toast.dataset.level = level;
    const icon = document.createElement('span');
    icon.className = 'toast-icon';
    icon.textContent = { success: '✓', warning: '!', error: '×', info: 'i' }[level] || 'i';
    const body = document.createElement('span');
    body.className = 'toast-text';
    body.textContent = text;
    toast.append(icon, body);
    list.append(toast);
    setup(toast);
    limit();
  };
})();
