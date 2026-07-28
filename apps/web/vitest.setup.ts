import "@testing-library/jest-dom/vitest";

// Node 22+ exposes an experimental global `localStorage` that, depending on
// the Node version running the suite, can shadow jsdom's own
// window.localStorage with a stub missing methods like removeItem (it needs
// a --localstorage-file to fully function). Define a minimal always-working
// in-memory Storage so token-storage tests behave the same on every Node
// version instead of depending on which implementation wins.
class MemoryStorage implements Storage {
  private store = new Map<string, string>();

  get length(): number {
    return this.store.size;
  }

  clear(): void {
    this.store.clear();
  }

  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null;
  }

  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

for (const target of [globalThis, globalThis.window]) {
  if (!target) continue;
  Object.defineProperty(target, "localStorage", {
    value: new MemoryStorage(),
    writable: true,
    configurable: true,
  });
}
