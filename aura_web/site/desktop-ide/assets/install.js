// Progressive enhancement only: the page and the setup-file download work with scripts off.
(function () {
  'use strict';

  // Copy buttons.
  document.querySelectorAll('[data-copy]').forEach(function (box) {
    var button = box.querySelector('.copy');
    var code = box.querySelector('code');
    if (!button || !code || !navigator.clipboard) {
      if (button) button.hidden = true;
      return;
    }
    button.addEventListener('click', function () {
      navigator.clipboard.writeText(code.textContent.trim()).then(function () {
        button.textContent = 'Copied';
        setTimeout(function () { button.textContent = 'Copy'; }, 1800);
      }, function () {
        button.textContent = 'Press Ctrl+C';
      });
    });
  });

  // The IDE is Windows-only: say so on anything else (the download still works, e.g. to carry
  // the file to a Windows PC).
  var platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
  if (platform && !/win/i.test(platform)) {
    var note = document.getElementById('platform-note');
    if (note) note.hidden = false;
  }

  // Which build is currently on offer: /desktop-ide/download/latest.json, written by the CI build
  // that deployed this site. Shown as text only (textContent), never as markup.
  var info = document.getElementById('build-info');
  if (!info || !window.fetch) return;
  fetch('/desktop-ide/download/latest.json', { cache: 'no-store' }).then(function (res) {
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }).then(function (m) {
    if (!m || !m.version || !m.installer || !m.installer.sha256) throw new Error('bad manifest');
    var parts = ['Latest build ' + m.version];
    var built = m.builtAt ? new Date(m.builtAt) : null;
    if (built && !isNaN(built.getTime())) {
      parts.push('built ' + built.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }));
    }
    if (typeof m.installer.size === 'number') parts.push((m.installer.size / 1048576).toFixed(0) + ' MB');
    info.textContent = parts.join(' · ');

    var direct = document.getElementById('direct');
    if (direct) direct.hidden = false;
    var shaBox = document.getElementById('installer-sha-box');
    var sha = document.getElementById('installer-sha');
    if (shaBox && sha) {
      sha.textContent = m.installer.sha256;
      shaBox.hidden = false;
    }
  }).catch(function () {
    info.textContent = 'No build has been published yet — check back soon.';
  });
})();
