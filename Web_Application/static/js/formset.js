document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('add-item').closest('form');
  const total = form.querySelector('input[name$="-TOTAL_FORMS"]');
  const template = document.getElementById('item-template');

  // Number the visible cards in order
  const renumber = () => form.querySelectorAll('.item-card:not(.is-removed) .item-number')
    .forEach((span, i) => { span.textContent = i + 1; });

  document.getElementById('add-item').addEventListener('click', (e) => {
    const wrap = document.createElement('div');
    wrap.innerHTML = template.innerHTML.replaceAll('__prefix__', total.value);
    e.target.closest('.form-actions').before(wrap.firstElementChild);
    total.value = Number(total.value) + 1;
    renumber();
  });

  // Delegated so it also works for cards added later
  form.addEventListener('click', (e) => {
    const button = e.target.closest('.remove-item');
    if (!button) return;
    const card = button.closest('.item-card');
    card.querySelector('input[name$="-DELETE"]').checked = true;
    card.querySelectorAll('[required]').forEach((input) => input.removeAttribute('required'));
    card.classList.add('is-removed');
    renumber();
  });
});
