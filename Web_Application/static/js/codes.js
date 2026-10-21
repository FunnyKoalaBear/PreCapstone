// Suggest the code type from the typed code until the user picks one
(function () {
  var form = document.querySelector('[data-code-form]');
  if (!form) return;
  var code = form.querySelector('[name="code"]'), kind = form.querySelector('[name="kind"]');
  var touched = kind.value !== '';
  kind.addEventListener('change', function () { touched = true; });
  code.addEventListener('input', function () {
    if (touched || !/^\d{5}$/.test(code.value)) return;
    kind.value = parseInt(code.value, 10) <= 99 ? 'INTERNAL' : 'VENDOR';
  });
})();
