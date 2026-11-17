'use strict';

// noVNC stores UI preferences in localStorage. Keep them in this iframe's
// memory when browser policy blocks site storage; reconnects still work.
(() => {
  try {
    const storage = window.localStorage;
    const probe = `desktop-storage-${Math.random()}`;
    storage.setItem(probe, probe);
    storage.removeItem(probe);
    return;
  } catch (_) { /* Site storage is unavailable. */ }

  const values = new Map();
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      get length() { return values.size; },
      key(index) { return Array.from(values.keys())[index] ?? null; },
      getItem(key) { return values.get(String(key)) ?? null; },
      setItem(key, value) { values.set(String(key), String(value)); },
      removeItem(key) { values.delete(String(key)); },
      clear() { values.clear(); },
    },
  });
})();
