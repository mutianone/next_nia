import type { SceneOptions, SpatialController } from './types';

/** Preserve the supplied portrait pixels; depth comes only from surrounding layers. */
export function createOfficerScene(host: HTMLElement, initial: SceneOptions, onFailure: () => void): SpatialController {
  const scene = document.createElement('div');
  scene.className = 'officer-scene';
  scene.innerHTML = '<div class="officer-depth"><div class="officer-halo"></div><i class="officer-orbit"></i><i class="officer-orbit officer-orbit-secondary"></i><div class="officer-lights"><i></i><i></i><i></i></div></div><div class="officer-ground"></div><div class="officer-portrait"></div>';
  const portrait = scene.querySelector<HTMLDivElement>('.officer-portrait')!;
  const image = document.createElement('img');
  image.alt = '小依：原图保留的面部与警服形象';
  image.draggable = false;
  image.src = '/characters/xiaoyi-reference.png';
  portrait.appendChild(image);
  host.appendChild(scene);

  let options = initial, disposed = false, visible = true, frame = 0, last = 0, time = 0;
  let targetX = 0, targetY = 0, hoverX = 0, hoverY = 0, x = 0, y = 0;
  const clamp = (value: number, limit: number) => Math.max(-limit, Math.min(limit, value));
  function stop() { if (frame) cancelAnimationFrame(frame); frame = 0; }
  function draw(now: number) {
    frame = 0;
    if (disposed || !visible || document.hidden) return;
    if (options.animated && now - last < 1000 / 30) { frame = requestAnimationFrame(draw); return; }
    const dt = Math.min((now - last) / 1000, .05);
    last = now;
    if (options.animated) time += dt;
    const blend = options.animated ? 1 - Math.exp(-dt * 9) : 1;
    x += (clamp(targetX + hoverX, 1) - x) * blend;
    y += (clamp(targetY + hoverY, 1) - y) * blend;
    // Translation only: no facial deformation, recoloring, invented blinking or side views.
    const drift = options.animated ? Math.sin(time * .8) * 2 : 0;
    const breath = options.animated ? Math.sin(time * 1.35) * 2.5 : 0;
    scene.style.setProperty('--portrait-x', `${x * 9 + drift}px`);
    scene.style.setProperty('--portrait-y', `${y * 4 + breath}px`);
    scene.style.setProperty('--depth-x', `${-x * 15 - drift * 2}px`);
    scene.style.setProperty('--depth-y', `${-y * 7 - breath * .5}px`);
    scene.style.setProperty('--orbit-angle', `${-18 + (options.animated ? Math.sin(time * .35) * 8 : 0) + x * 3}deg`);
    scene.style.setProperty('--glow-opacity', `${options.animated ? .65 + Math.sin(time * .65) * .18 : .65}`);
    if (options.animated) frame = requestAnimationFrame(draw);
  }
  function wake() {
    if (!disposed && visible && !document.hidden && !frame) {
      last = performance.now(); frame = requestAnimationFrame(draw);
    }
  }
  function applyOptions() {
    scene.dataset.framing = options.expanded ? 'portrait' : 'full';
    scene.style.setProperty('--officer-accent', options.accent);
  }
  const fail = () => { if (!disposed) { stop(); onFailure(); } };
  image.addEventListener('error', fail);
  const observer = new IntersectionObserver(entries => {
    visible = entries[0]?.isIntersecting ?? false;
    if (visible) wake(); else stop();
  }, { rootMargin: '80px' });
  observer.observe(host);
  const visibility = () => document.hidden ? stop() : wake();
  document.addEventListener('visibilitychange', visibility);
  applyOptions(); wake();
  return {
    update(next) { options = next; applyOptions(); stop(); wake(); },
    point(px, py) { hoverX = options.animated ? clamp(px, .5) : 0; hoverY = options.animated ? clamp(py, .5) : 0; wake(); },
    turn(dx, dy) { hoverX = hoverY = 0; targetX = clamp(targetX + dx, 1); targetY = clamp(targetY + dy, 1); wake(); },
    reset() { targetX = targetY = hoverX = hoverY = x = y = time = 0; wake(); },
    dispose() { disposed = true; stop(); observer.disconnect(); document.removeEventListener('visibilitychange', visibility); image.removeEventListener('error', fail); scene.remove(); },
  };
}
