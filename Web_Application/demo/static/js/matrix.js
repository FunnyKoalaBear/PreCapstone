// A click anywhere on a matrix row opens its order
document.addEventListener('click', (e) => {
  const row = e.target.closest('tr[data-href]');
  if (row && !e.target.closest('a')) window.location.href = row.dataset.href;
});
