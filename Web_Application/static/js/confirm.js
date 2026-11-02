// Confirm dialog: a form or submit button with data-confirm asks first (data-confirm-danger = red button)
(() => {
  const dialog = document.getElementById('confirm-dialog');
  if (!dialog || typeof dialog.showModal !== 'function') return; // without it the form just submits
  const text = dialog.querySelector('#confirm-message');
  const ok = dialog.querySelector('#confirm-ok');

  // data-confirm-if-unchecked="field": ask only when that box was ticked and is now cleared
  const message = (form, source) => {
    const gate = form.dataset.confirmIfUnchecked;
    if (gate) {
      const box = form.querySelector(`[name="${gate}"]`);
      if (!box || box.checked || !box.defaultChecked) return '';
    }
    return source.dataset.confirm || '';
  };

  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (form._confirmed) { form._confirmed = false; return; }
    const source = e.submitter && e.submitter.dataset.confirm ? e.submitter : form;
    const ask = message(form, source);
    if (!ask) return;
    e.preventDefault();
    text.textContent = ask;
    ok.className = source.hasAttribute('data-confirm-danger') ? 'button-danger' : 'button';
    ok.textContent = source.dataset.confirmButton || dialog.dataset.confirm;
    dialog.returnValue = '';
    dialog.addEventListener('close', () => {
      if (dialog.returnValue !== 'ok') return;
      form._confirmed = true;
      form.requestSubmit(e.submitter || undefined);
    }, { once: true });
    dialog.showModal();
  }, true);
})();
