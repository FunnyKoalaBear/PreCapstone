document.addEventListener('DOMContentLoaded', () => {
  const button = document.querySelector('.nav-toggle');
  const nav = button && document.getElementById(button.getAttribute('aria-controls'));
  if (!nav) return;

  const setOpen = (open) => {
    nav.classList.toggle('is-open', open);
    button.setAttribute('aria-expanded', String(open));
  };

  button.addEventListener('click', () => setOpen(!nav.classList.contains('is-open')));

  // Escape closes the menu and puts focus back on the button
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && nav.classList.contains('is-open')) {
      setOpen(false);
      button.focus();
    }
  });
});
