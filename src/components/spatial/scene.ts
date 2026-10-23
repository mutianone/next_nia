import * as T from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import type { SceneOptions, SpatialController, SpatialVariant } from './types';

// Every object is generated locally. No models, textures, fonts or network loaders.
export function createSpatialScene(host: HTMLElement, variant: SpatialVariant, initial: SceneOptions, onFailure: () => void): SpatialController {
  const renderer = new T.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.outputColorSpace = T.SRGBColorSpace;
  renderer.toneMapping = T.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.2;
  renderer.domElement.setAttribute('aria-hidden', 'true');
  host.appendChild(renderer.domElement);
  const scene = new T.Scene();
  const camera = new T.OrthographicCamera(-4, 4, 3, -3, .1, 60);
  camera.position.set(6, 4.8, 8); camera.lookAt(0, .55, 0);
  const world = new T.Group(); scene.add(world);
  const ambient = new T.HemisphereLight('#ecfaff', '#315062', 2.7); scene.add(ambient);
  const light = new T.DirectionalLight('#ffffff', 3.5); light.position.set(4, 7, 5); scene.add(light);
  const rim = new T.DirectionalLight('#99ffe1', 2); rim.position.set(-5, 2, -2); scene.add(rim);
  const mint = new T.MeshStandardMaterial({ color: initial.accent, roughness: .27, metalness: .42, emissive: initial.accent, emissiveIntensity: .15 });
  const white = new T.MeshStandardMaterial({ color: '#e4f3f6', roughness: .22, metalness: .28 });
  const dark = new T.MeshStandardMaterial({ color: initial.primary, roughness: .32, metalness: .55 });
  const glass = new T.MeshStandardMaterial({ color: initial.accent, transparent: true, opacity: .28, roughness: .16, metalness: .22, depthWrite: false });
  const glow = new T.MeshBasicMaterial({ color: initial.accent });
  const wire = new T.LineBasicMaterial({ color: initial.accent, transparent: true, opacity: .4 });
  const materials = new Set<T.Material>([mint, white, dark, glass, glow, wire]);
  const geometries = new Set<T.BufferGeometry>();
  const floating: { object: T.Group; base: T.Vector3; phase: number }[] = [];
  const spinning: { object: T.Object3D; axis: 'y' | 'z'; speed: number }[] = [];
  const orbiters: { object: T.Object3D; angle: number; radius: number; height: number }[] = [];
  const waves: { object: T.Object3D; phase: number; height: number }[] = [];
  const mesh = (geometry: T.BufferGeometry, material: T.Material, parent: T.Object3D, x = 0, y = 0, z = 0) => {
    geometries.add(geometry); const object = new T.Mesh(geometry, material); object.position.set(x, y, z); parent.add(object); return object;
  };
  const box = (parent: T.Object3D, w: number, h: number, d: number, material: T.Material, x = 0, y = 0, z = 0) => mesh(new RoundedBoxGeometry(w, h, d, 2, Math.min(w, h, d) * .13), material, parent, x, y, z);
  const ring = (parent: T.Object3D, radius: number, thickness: number, x = 0, y = 0, z = 0) => mesh(new T.TorusGeometry(radius, thickness, 8, 64), mint, parent, x, y, z);
  const sphere = (parent: T.Object3D, radius: number, x: number, y: number, z: number) => mesh(new T.SphereGeometry(radius, 20, 12), mint, parent, x, y, z);
  const line = (points: T.Vector3[], parent: T.Object3D = world) => { const geometry = new T.BufferGeometry().setFromPoints(points); geometries.add(geometry); parent.add(new T.Line(geometry, wire)); };
  const cluster = (x: number, y: number, z: number) => { const group = new T.Group(); group.position.set(x, y, z); world.add(group); floating.push({ object: group, base: group.position.clone(), phase: floating.length * 1.7 }); return group; };
  const outline = (object: T.Mesh) => { const geometry = new T.EdgesGeometry(object.geometry, 30); geometries.add(geometry); object.add(new T.LineSegments(geometry, wire)); };
  const floor = new T.Group(); world.add(floor);
  box(floor, 4.6, .14, 3.4, dark, 0, -.77, 0);
  box(floor, 4.25, .07, 3.08, glass, 0, -.58, 0);
  for (let i = -2; i <= 2; i++) {
    line([new T.Vector3(i * .8, -.53, -1.4), new T.Vector3(i * .8, -.53, 1.4)], floor);
    line([new T.Vector3(-1.9, -.53, i * .6), new T.Vector3(1.9, -.53, i * .6)], floor);
  }
  const halo = ring(floor, 2.65, .018, 0, -.8, 0); halo.rotation.x = -Math.PI / 2;
  // Layered machined base: edge rails, recessed light strips, screws and orbit ticks.
  [-1, 1].forEach(sign => {
    box(floor, 4.32, .035, .035, glow, 0, -.7, sign * 1.63);
    box(floor, .035, .035, 3.1, mint, sign * 2.2, -.7, 0);
    [-1, 1].forEach(other => mesh(new T.CylinderGeometry(.065, .065, .025, 12), white, floor, sign * 2.05, -.67, other * 1.46));
  });
  for (let i = 0; i < 48; i++) {
    const angle = i / 48 * Math.PI * 2;
    const tick = box(floor, i % 4 ? .025 : .045, .018, i % 4 ? .06 : .13, i % 4 ? dark : mint, Math.cos(angle) * 2.7, -.81, Math.sin(angle) * 2.7);
    tick.rotation.y = -angle + Math.PI / 2;
  }
  const orbit = new T.Group(); world.add(orbit);
  const arc = mesh(new T.TorusGeometry(2.45, .022, 8, 80, Math.PI * 1.35), glow, orbit); arc.rotation.x = Math.PI / 2; arc.position.y = -.37;
  spinning.push({ object: orbit, axis: 'y', speed: .34 });
  for (let i = 0; i < 5; i++) {
    const object = mesh(new T.OctahedronGeometry(i ? .055 : .095), i ? mint : white, world);
    object.position.set(Math.cos(i * 1.25) * 2.3, -.25 + i * .12, Math.sin(i * 1.25) * 2.3);
    orbiters.push({ object, angle: i * 1.25, radius: 2.3 + i * .05, height: -.25 + i * .12 });
  }

  const page = (parent: T.Object3D, x: number, y: number, z: number, width = 1.3, height = 1.7) => {
    const group = new T.Group(); group.position.set(x, y, z); parent.add(group);
    box(group, width, height, .12, white);
    box(group, width * .2, .25, .02, mint, -width * .28, height * .31, .075);
    for (let i = 0; i < 3; i++) box(group, width * .35, .022, .02, dark, width * .17, height * .36 - i * .07, .075);
    box(group, width * .7, .018, .02, mint, 0, -height * .38, .075);
    const fold = mesh(new T.BufferGeometry().setFromPoints([new T.Vector3(0,0,0), new T.Vector3(-.22,0,0), new T.Vector3(0,-.22,0)]), mint, group, width / 2, height / 2, .075); fold.geometry.computeVertexNormals();
    for (let i = 0; i < 4; i++) box(group, width * (i === 3 ? .38 : .68), .035, .02, dark, i === 3 ? -width * .15 : 0, height * .1 - i * .18, .075);
    return group;
  };
  if (variant === 'analytics') {
    const towers = [[-1.3, .8, 0], [-.45, 1.35, 0], [.45, 1.85, 0], [1.3, 2.4, 0]];
    towers.forEach(([x, height, z], index) => {
      const group = cluster(x, -.42 + height / 2, z);
      outline(box(group, .77, height, .78, index === 1 ? glass : dark));
      box(group, .8, .1, .8, index === 1 ? white : mint, 0, height / 2, 0);
      for (let row = 0; row < Math.floor(height / .24); row++) {
        box(group, .5, .025, .02, glow, 0, -height / 2 + .18 + row * .24, .404);
        for (let column = 0; column < 3; column++) box(group, .02, .07, .09, (row + column) % 3 ? mint : white, .397, -height / 2 + .18 + row * .24, -.25 + column * .23);
      }
      box(group, .6, .04, .6, dark, 0, height / 2 + .08, 0);
      box(group, .025, .3, .025, white, .2, height / 2 + .2, -.2);
      sphere(group, .045, .2, height / 2 + .37, -.2);
    });
    const beacon = cluster(.1, 2.5, -.35); mesh(new T.OctahedronGeometry(.22), white, beacon);
    spinning.push({ object: beacon.children[0], axis: 'y', speed: .6 });
    line([new T.Vector3(-1.9, .05, 1.1), new T.Vector3(-.9, .5, 1.1), new T.Vector3(.35, .8, 1.1), new T.Vector3(1.9, 1.7, 1.1)]);
  } else if (variant === 'knowledge' || variant === 'documents') {
    [-1.15, 0, 1.15].forEach((x, i) => { const group = cluster(x, .85 + (i === 1 ? .25 : 0), i === 1 ? .3 : -.25); const sheet = page(group, 0, 0, 0, 1.12, 1.65); sheet.rotation.y = (i - 1) * -.28; box(group, 1.18, 1.7, .08, mint, -.06, -.06, -.13); });
    const check = cluster(1.6, .15, 1); const seal = mesh(new T.CylinderGeometry(.35, .35, .1, 40), mint, check); seal.rotation.x = Math.PI / 2;
    const a = box(check, .1, .27, .07, white, -.1, -.03, .08); a.rotation.z = .65;
    const b = box(check, .1, .48, .07, white, .09, .04, .08); b.rotation.z = -.65;
  } else if (variant === 'people') {
    [-1.1, .1, 1.25].forEach((x, i) => {
      const card = cluster(x, .85 + (i === 1 ? .3 : 0), (i - 1) * -.38); card.rotation.y = -.15;
      box(card, 1.4, 1.8, .14, i === 1 ? white : dark);
      const head = mesh(new T.CylinderGeometry(.2, .2, .055, 32), mint, card, 0, .35, .1); head.rotation.x = Math.PI / 2;
      box(card, .57, .23, .06, mint, 0, -.02, .11);
      box(card, .85, .035, .03, i === 1 ? dark : white, 0, -.42, .1);
      box(card, .5, .035, .03, i === 1 ? dark : white, -.17, -.61, .1);
    });
  } else if (variant === 'records') {
    const mic = cluster(0, .9, 0); mesh(new T.CapsuleGeometry(.35, .78, 5, 24), white, mic, 0, .35, 0);
    for (let i = 0; i < 8; i++) box(mic, .5, .025, .025, dark, 0, .8 - i * .12, .345);
    for (let i = -2; i <= 2; i++) box(mic, .018, .9, .02, mint, i * .09, .38, .365);
    sphere(mic, .045, 0, -.25, .35);
    const collar = ring(mic, .37, .025, 0, -.16, 0); collar.rotation.x = Math.PI / 2;
    const cradle = mesh(new T.TorusGeometry(.57, .045, 8, 40, Math.PI), mint, mic); cradle.rotation.z = Math.PI;
    box(mic, .08, .5, .08, mint, 0, -.78, 0); box(mic, .65, .09, .43, white, 0, -1.04, 0);
    for (let i = 0; i < 14; i++) {
      const x = (i < 7 ? -2.1 : .65) + i % 7 * .23;
      const height = .25 + Math.sin((i % 7 + 1) / 8 * Math.PI) * .85;
      const bar = box(world, .07, height, .12, i % 3 ? mint : white, x, .6, .25); waves.push({ object: bar, phase: i * .7, height });
    }
  } else if (variant === 'alerts') {
    const radar = cluster(0, -.1, 0);
    [ .65, 1.25, 1.85 ].forEach(radius => { const r = ring(radar, radius, .022); r.rotation.x = -Math.PI / 2; });
    const sweep = new T.Group(); radar.add(sweep);
    const wedge = mesh(new T.CircleGeometry(1.82, 48, 0, Math.PI / 2), glass, sweep, 0, .025, 0); wedge.rotation.x = -Math.PI / 2; glass.side = T.DoubleSide;
    box(sweep, .025, .035, 1.85, mint, 0, .03, -.925); spinning.push({ object: sweep, axis: 'y', speed: -.55 });
    [[-1.1, .7, .4], [.9, 1.3, -.5], [1.2, .5, .9]].forEach(([x, y, z]) => {
      const pin = cluster(x, y, z); sphere(pin, .15, 0, 0, 0); box(pin, .025, y + .1, .025, mint, 0, -(y + .1) / 2, 0);
      const r = ring(pin, .3, .018); r.rotation.x = -Math.PI / 2;
    });
  } else if (variant === 'settings') {
    const group = cluster(0, .85, 0); box(group, 2.9, 1.7, .22, dark);
    for (let i = 0; i < 3; i++) {
      box(group, 2.3, .035, .03, white, 0, .5 - i * .5, .14);
      const knob = mesh(new T.CylinderGeometry(.18, .18, .12, 32), mint, group, [ -.6, .5, -.1 ][i], .5 - i * .5, .23); knob.rotation.x = Math.PI / 2;
    }
    const cog = cluster(1.75, .2, .8); const r = ring(cog, .35, .12); spinning.push({ object: r, axis: 'z', speed: .35 });
  } else if (variant === 'appearance') {
    const colors = ['#79c5bb', '#b3be91', '#40c9e8', '#c1a1df', '#afb8b6'];
    colors.forEach((color, i) => { const material = new T.MeshStandardMaterial({ color, roughness: .28, metalness: .2 }); materials.add(material); const swatch = cluster((i - 2) * .52, .9, 0); swatch.rotation.z = (i - 2) * -.16; box(swatch, .75, 1.85, .15, material); box(swatch, .76, .4, .16, white, 0, -.73, .015); });
  } else {
    const core = cluster(0, .9, 0); mesh(new T.IcosahedronGeometry(.65, 1), white, core);
    const r1 = ring(core, 1.05, .025); r1.rotation.x = .55; const r2 = ring(core, 1.4, .02); r2.rotation.y = 1.1;
    spinning.push({ object: r1, axis: 'y', speed: .22 }, { object: r2, axis: 'z', speed: -.18 });
    for (let i = 0; i < 6; i++) { const angle = i * Math.PI / 3; const x = Math.cos(angle) * 1.9, y = .9 + Math.sin(angle) * .8, z = Math.sin(angle) * 1.2; const node = cluster(x, y, z); sphere(node, .15, 0, 0, 0); line([new T.Vector3(0, .9, 0), new T.Vector3(x, y, z)]); }
  }
  // Floating markers add depth without carrying or inventing business data.
  for (let i = 0; i < 7; i++) { const marker = mesh(new T.OctahedronGeometry(.035), glow, world, Math.cos(i * 2.4) * 2.6, .2 + (i % 4) * .55, Math.sin(i * 2.4) * 1.8); spinning.push({ object: marker, axis: 'y', speed: .2 }); }

  let options = initial, disposed = false, visible = true, frame = 0, last = 0, elapsed = 0, expansion = 0;
  let yaw = -.28, pitch = 0, hoverX = 0, hoverY = 0;
  let targetYaw = yaw, targetPitch = pitch;
  const render = () => {
    if (disposed) return;
    try { renderer.render(scene, camera); } catch { stop(); onFailure(); }
  };
  const draw = (time: number) => {
    frame = 0; if (disposed || !visible || document.hidden) return;
    // Limit the decorative scene to 30 fps. Business controls keep their own cadence.
    if (options.animated && time - last < 1000 / 30) { frame = requestAnimationFrame(draw); return; }
    const dt = Math.min((time - last) / 1000 || 0, .05); last = time;
    if (options.animated) { elapsed += dt; targetYaw += dt * .16; }
    const blend = options.animated ? 1 - Math.exp(-dt * 9) : 1;
    yaw += (targetYaw + hoverX * .15 - yaw) * blend;
    pitch += (targetPitch + hoverY * .08 - pitch) * blend;
    expansion += ((options.expanded ? 1 : 0) - expansion) * blend;
    world.rotation.set(pitch, yaw, 0);
    floating.forEach(({ object, base, phase }) => { object.position.copy(base); object.position.x *= 1 + expansion * .25; object.position.z *= 1 + expansion * .4; object.position.y += expansion * .12 + (options.animated ? Math.sin(elapsed * 1.5 + phase) * .13 : 0); });
    if (options.animated) {
      spinning.forEach(({ object, axis, speed }) => { object.rotation[axis] += dt * speed; });
      orbiters.forEach(({ object, angle, radius, height }) => { object.position.set(Math.cos(elapsed * .55 + angle) * radius, height + Math.sin(elapsed * 1.4 + angle) * .15, Math.sin(elapsed * .55 + angle) * radius); object.rotation.y += dt; });
      waves.forEach(({ object, phase }) => { object.scale.y = .75 + Math.sin(elapsed * 2 + phase) * .25; });
    }
    render(); if (options.animated && !disposed) frame = requestAnimationFrame(draw);
  };
  function stop() { if (frame) cancelAnimationFrame(frame); frame = 0; }
  function wake() { if (!disposed && visible && !document.hidden && !frame) { last = performance.now(); frame = requestAnimationFrame(draw); } }
  const resize = () => {
    const { width, height } = host.getBoundingClientRect(); if (!width || !height) return;
    const halfHeight = 2.55, aspect = width / height;
    camera.left = -halfHeight * aspect; camera.right = halfHeight * aspect; camera.top = halfHeight; camera.bottom = -halfHeight; camera.updateProjectionMatrix();
    world.scale.setScalar(aspect < 1.25 ? .8 : 1);
    renderer.setSize(width, height, false); wake();
  };
  const resizeObserver = new ResizeObserver(resize); resizeObserver.observe(host);
  const visibility = () => { if (document.hidden) stop(); else wake(); };
  document.addEventListener('visibilitychange', visibility);
  const intersection = new IntersectionObserver(entries => { visible = entries[0]?.isIntersecting ?? false; if (visible) wake(); else stop(); }, { rootMargin: '80px' }); intersection.observe(host);
  const contextLost = (event: Event) => { event.preventDefault(); stop(); onFailure(); };
  renderer.domElement.addEventListener('webglcontextlost', contextLost);
  resize(); wake();
  return {
    update(next) { options = next; mint.color.set(next.accent); mint.emissive.set(next.accent); glass.color.set(next.accent); glow.color.set(next.accent); wire.color.set(next.accent); dark.color.set(next.primary); rim.color.set(next.accent); stop(); wake(); },
    point(x, y) { hoverX = options.animated ? x : 0; hoverY = options.animated ? y : 0; wake(); },
    turn(x, y) { hoverX = 0; hoverY = 0; targetYaw += x; targetPitch = T.MathUtils.clamp(targetPitch + y, -.3, .4); wake(); },
    reset() { targetYaw = -.28; targetPitch = 0; hoverX = hoverY = 0; wake(); },
    dispose() { disposed = true; stop(); resizeObserver.disconnect(); intersection.disconnect(); document.removeEventListener('visibilitychange', visibility); renderer.domElement.removeEventListener('webglcontextlost', contextLost); geometries.forEach(item => item.dispose()); materials.forEach(item => item.dispose()); renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove(); },
  };
}
