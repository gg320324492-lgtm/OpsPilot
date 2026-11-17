# v0.2.3 — pinned top bar

This is a one-line UI fix on top of v0.2.2. No API, permission, state-machine,
retrieval, model, or approval behaviour changed.

## Fixed

- The dashboard's own top bar doubles as the frameless Electron window's
  title bar. It was an ordinary element in document flow, so scrolling a long
  page (for example the Knowledge document list) scrolled the navigation and
  language switcher out of view while the native caption buttons stayed
  fixed. The bar is now `sticky top-0 z-40`, keeping the navigation and the
  window chrome together at the top of the window.

## Verified

- `e460e70`: CI green (lint, unit, integration, web-e2e, desktop-package).
- The fix was built into a local verification installer and exercised by the
  project owner, who confirmed by scrolling a long page that the top bar now
  stays fixed.
- The independent v0.2.2 installation walk remains archived in
  [`installer-verification.md`](installer-verification.md); this patch does
  not change the installer or the backend.

## Scope

Everything shipped in v0.2.2 still applies: the HeroUI semantic-token UI, the
frameless shell, the accessibility fixes, and the re-captured README GIF and
screenshots. The only source change after v0.2.2 is the sticky positioning of
the navigation bar plus the version bump.
