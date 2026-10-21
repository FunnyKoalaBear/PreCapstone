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

  // Append an empty copy of the last route step
  const addStep = (steps) => {
    const row = steps.lastElementChild.cloneNode(true);
    row.querySelectorAll('select, input').forEach((s) => { s.value = ''; });
    steps.append(row);
    return row;
  };

  // Route steps: delegated so they work in cards added later
  form.addEventListener('click', (e) => {
    const add = e.target.closest('.add-step');
    const remove = e.target.closest('.remove-step');
    if (add) {
      addStep(add.closest('.route').querySelector('.route-steps'));
    } else if (remove) {
      const row = remove.closest('.route-step');
      if (row.parentElement.children.length > 1) row.remove();
      else row.querySelectorAll('select, input').forEach((s) => { s.value = ''; });
    }
  });

  // "Copy route from last order" needs a part number
  const refreshCopy = (card) => {
    const button = card.querySelector('.copy-route');
    const part = card.querySelector('input[name$="-part_number"]');
    if (button && part) button.disabled = !part.value.trim();
  };
  form.querySelectorAll('.item-card').forEach(refreshCopy);
  form.addEventListener('input', (e) => {
    if (e.target.matches('input[name$="-part_number"]')) refreshCopy(e.target.closest('.item-card'));
  });

  form.addEventListener('click', async (e) => {
    const button = e.target.closest('.copy-route');
    if (!button) return;
    const card = button.closest('.item-card');
    const message = card.querySelector('.copy-route-message');
    const id = card.querySelector('input[name$="-id"]');
    const params = new URLSearchParams({ part_number: card.querySelector('input[name$="-part_number"]').value });
    if (id && id.value) params.set('item', id.value);
    const response = await fetch(`${button.dataset.url}?${params}`);
    if (!response.ok) return;
    const data = await response.json();
    if (!data.found) { message.textContent = button.dataset.none; return; }
    const steps = card.querySelector('.route-steps');
    const filled = [...steps.querySelectorAll('select[name$="-route"]')].some((s) => s.value);
    if (filled && !confirm(button.dataset.confirm)) return;
    while (steps.children.length > 1) steps.lastElementChild.remove();
    steps.firstElementChild.querySelectorAll('select, input').forEach((s) => { s.value = ''; });
    data.steps.forEach((step, i) => {
      const row = i ? addStep(steps) : steps.firstElementChild;
      row.querySelector('select[name$="-route"]').value = step.process;
      row.querySelector('select[name$="-route_vendor"]').value = step.vendor ?? '';
    });
    message.textContent = button.dataset.copied.replace('{job}', data.job)
      + (data.skipped ? ` ${button.dataset.skipped.replace('{n}', data.skipped)}` : '');
  });

  // Picking a process prefills the vendor with the process default
  form.addEventListener('change', (e) => {
    if (!e.target.matches('select[name$="-route"]')) return;
    const vendor = e.target.closest('.route-step').querySelector('select[name$="-route_vendor"]');
    vendor.value = e.target.selectedOptions[0].dataset.vendor || '';
  });
});
