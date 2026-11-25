// Tags page: tick rows to release; the button shows the count and the confirm text uses it
(() => {
  const form = document.querySelector('[data-bulk-release]');
  if (!form) return;
  const boxes = () => [...document.querySelectorAll('input[name="assignment"][form="bulk-release"]')];
  const all = document.querySelector('[data-select-all]');
  const button = form.querySelector('button');

  const update = () => {
    const ticked = boxes().filter((b) => b.checked);
    const working = ticked.filter((b) => b.hasAttribute('data-working')).length;
    form.querySelector('[data-count]').textContent = ticked.length;
    button.disabled = !ticked.length;
    if (all) all.checked = ticked.length > 0 && ticked.length === boxes().length;
    let text = form.dataset.one.replace('{n}', ticked.length);
    if (working) text += '\n' + form.dataset.working.replace('{k}', working);
    button.dataset.confirm = text;
  };

  if (all) all.addEventListener('change', () => { boxes().forEach((b) => { b.checked = all.checked; }); update(); });
  document.addEventListener('change', (e) => { if (e.target.matches('input[name="assignment"]')) update(); });
  update();
})();
