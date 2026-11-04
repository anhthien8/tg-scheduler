// PWA: register the service worker and offer a one-tap "install app" bottom sheet.
// Android/Chrome/Edge: uses the native beforeinstallprompt → 1 click installs.
// iOS Safari has no install API → the sheet shows the Share → "Add to Home Screen" steps.
const PWA = {
  deferred: null,
  DISMISS_KEY: 'tgs_pwa_dismissed_at',
  DISMISS_DAYS: 7,

  init() {
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    if (this.isStandalone()) return;

    window.addEventListener('beforeinstallprompt', e => {
      e.preventDefault();
      this.deferred = e;
      if (this.isMobile() && !this.recentlyDismissed()) this.show(false);
    });
    window.addEventListener('appinstalled', () => { this.hide(); this.deferred = null; });

    const iOS = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
    if (iOS && !this.recentlyDismissed()) setTimeout(() => this.show(true), 1500);
  },

  isStandalone() {
    return window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  },
  isMobile() { return window.matchMedia('(max-width: 768px), (pointer: coarse)').matches; },
  recentlyDismissed() {
    const t = Number(localStorage.getItem(this.DISMISS_KEY) || 0);
    return Date.now() - t < this.DISMISS_DAYS * 86400000;
  },

  show(iOS) {
    const el = document.getElementById('pwa-install-sheet');
    if (!el) return;
    el.querySelector('.pwa-ios-steps').hidden = !iOS;
    el.querySelector('#pwa-install-btn').hidden = iOS;
    el.classList.add('open');
  },
  hide() { document.getElementById('pwa-install-sheet')?.classList.remove('open'); },
  dismiss() { localStorage.setItem(this.DISMISS_KEY, String(Date.now())); this.hide(); },

  async install() {
    if (!this.deferred) return this.hide();
    this.deferred.prompt();
    const { outcome } = await this.deferred.userChoice;
    this.deferred = null;
    if (outcome === 'accepted') this.hide(); else this.dismiss();
  },
};
PWA.init();
