// Sticky summary of the ticked record rows: count, total, mean, median.
(function () {
  var box = document.querySelector('[data-records-summary]');
  if (!box) return;
  var checks = Array.prototype.slice.call(document.querySelectorAll('.record-check'));
  var all = document.querySelector('[data-check-all]');
  var days = box.dataset.unit === 'days';
  var out = function (k) { return box.querySelector('[data-out="' + k + '"]'); };

  function fmt(v) {
    if (days) return (Math.round(v * 10) / 10).toString();
    var mins = Math.floor(v / 60), d = Math.floor(mins / 1440), h = Math.floor(mins / 60) % 24, m = mins % 60;
    var f = d ? box.dataset.fmtDh : h ? box.dataset.fmtHm : m ? box.dataset.fmtM : box.dataset.fmtLt1m;
    return f.replace('%(d)d', d).replace('%(h)d', h).replace('%(m)d', m);
  }

  function update() {
    var ticked = checks.filter(function (c) { return c.checked; });
    var vals = ticked.map(function (c) { return parseFloat(c.dataset.value); }).filter(function (v) { return !isNaN(v); }).sort(function (a, b) { return a - b; });
    var n = vals.length, sum = vals.reduce(function (a, b) { return a + b; }, 0), mid = Math.floor(n / 2);
    out('count').textContent = ticked.length;
    out('total').textContent = n ? fmt(sum) : '-';
    out('mean').textContent = n ? fmt(sum / n) : '-';
    out('median').textContent = n ? fmt(n % 2 ? vals[mid] : (vals[mid - 1] + vals[mid]) / 2) : '-';
  }

  checks.forEach(function (c) { c.addEventListener('change', update); });
  if (all) all.addEventListener('change', function () { checks.forEach(function (c) { c.checked = all.checked; }); update(); });
  update();
})();
