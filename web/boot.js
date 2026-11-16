// Apply saved appearance before the stylesheet paints, so there is no flash.
try {
  var theme = localStorage.getItem('murmur.theme') || 'ink';
  if (window.applyRhapsodeTheme) window.applyRhapsodeTheme(theme);

  var font = localStorage.getItem('murmur.font');
  if (font && window.rhapsodeFontStack) {
    var stack = window.rhapsodeFontStack(font);
    if (stack) document.documentElement.style.setProperty('--font-read', stack);
  }
} catch (e) {}
