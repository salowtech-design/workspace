// Salow Workspace Service Worker Registration
// Explicitly registers root-scoped /sw.js with scope '/'
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
  });
}
