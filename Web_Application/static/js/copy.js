document.addEventListener('DOMContentLoaded', () => {
  const button = document.getElementById('copy-password');
  const text = document.getElementById('temp-password');
  if (button && text) button.addEventListener('click', () => navigator.clipboard.writeText(text.textContent.trim()));
});
