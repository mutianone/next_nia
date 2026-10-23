import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createOfficerScene } from './officerScene';

let pending = new Map<number, FrameRequestCallback>(), id = 0;
const disconnect = vi.fn();
const options = { animated: false, expanded: true, accent: '#abcabc', primary: '#123456' };
function step(now = 100) { const callbacks = [...pending.values()]; pending.clear(); callbacks.forEach(callback => callback(now)); }
beforeEach(() => {
  pending = new Map(); id = 0; disconnect.mockClear();
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { pending.set(++id, callback); return id; });
  vi.stubGlobal('cancelAnimationFrame', (key: number) => pending.delete(key));
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect = disconnect; });
});
afterEach(() => vi.unstubAllGlobals());

it('uses the original asset, bounds manual parallax, and restores the front view', () => {
  const host = document.createElement('div');
  const scene = createOfficerScene(host, options, vi.fn());
  const root = host.querySelector<HTMLElement>('.officer-scene')!;
  expect(host.querySelector('img')?.getAttribute('src')).toBe('/characters/xiaoyi-reference.png');
  scene.turn(999, -999); step();
  expect(root.style.getPropertyValue('--portrait-x')).toBe('9px');
  expect(root.style.getPropertyValue('--portrait-y')).toBe('-4px');
  expect(pending.size).toBe(0);
  scene.reset(); step();
  expect(root.style.getPropertyValue('--portrait-x')).toBe('0px');
  scene.update({ ...options, expanded: false }); step();
  expect(root.dataset.framing).toBe('full');
  scene.dispose(); expect(host.childElementCount).toBe(0); expect(disconnect).toHaveBeenCalledOnce();
});

it('animates while enabled, cancels on dispose, and reports failed image loading', () => {
  const host = document.createElement('div'), failure = vi.fn();
  const scene = createOfficerScene(host, { ...options, animated: true }, failure);
  step(performance.now() + 100);
  expect(pending.size).toBe(1);
  const image = host.querySelector('img')!;
  image.dispatchEvent(new Event('error')); expect(failure).toHaveBeenCalledOnce();
  scene.dispose(); expect(pending.size).toBe(0);
  image.dispatchEvent(new Event('error')); expect(failure).toHaveBeenCalledOnce();
});
