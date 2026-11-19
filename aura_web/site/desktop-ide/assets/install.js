// Progressive enhancement only: the page and the download work with scripts off.
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
})();
