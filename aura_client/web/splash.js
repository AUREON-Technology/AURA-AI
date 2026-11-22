// Removes the loading splash once Flutter has painted. Flutter dispatches `flutter-first-frame`
// on the window; a timeout is the fallback so a failed boot never leaves the splash covering an
// error forever.
(function () {
  function remove() {
    var el = document.getElementById('aura-splash');
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }
  window.addEventListener('flutter-first-frame', remove);
  window.setTimeout(remove, 30000);
})();
