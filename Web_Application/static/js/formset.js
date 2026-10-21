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

  // Route steps: delegated so they work in cards added later
  form.addEventListener('click', (e) => {
    const add = e.target.closest('.add-step');
    const remove = e.target.closest('.remove-step');
    if (add) {
      const steps = add.closest('.route').querySelector('.route-steps');
      const row = steps.lastElementChild.cloneNode(true);
      row.querySelectorAll('select').forEach((s) => { s.value = ''; });
      steps.append(row);
    } else if (remove) {
      const row = remove.closest('.route-step');
      if (row.parentElement.children.length > 1) row.remove();
      else row.querySelectorAll('select').forEach((s) => { s.value = ''; });
    }
  });

  // Picking a process prefills the vendor with the process default
  form.addEventListener('change', (e) => {
    if (!e.target.matches('select[name$="-route"]')) return;
    const vendor = e.target.closest('.route-step').querySelector('select[name$="-route_vendor"]');
    vendor.value = e.target.selectedOptions[0].dataset.vendor || '';
  });
});
