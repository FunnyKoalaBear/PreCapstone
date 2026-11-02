// Style guide: buttons that fire demo toasts
document.querySelectorAll('[data-toast]').forEach((button) => {
  button.addEventListener('click', () => window.FT.toast(button.dataset.toast, `This is a ${button.dataset.toast} toast.`));
});
