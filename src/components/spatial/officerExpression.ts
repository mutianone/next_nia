/** Small image-space movements anchored to the supplied portrait, in source pixels. */
export const FACE = { x: 360, y: 183, width: 154, height: 145 };
const eyes = [{ x: 395, y: 218, rx: 29, ry: 20 }, { x: 478, y: 218, rx: 29, ry: 20 }];
const clamp01 = (n: number) => Math.max(0, Math.min(1, n));
const smooth = (n: number) => { const t = clamp01(n); return t * t * (3 - 2 * t); };

export function expressionAt(time: number, animated: boolean) {
  if (!animated) return { blink: 0, smile: 0 };
  const phase = time % 4.8;
  const blink = phase > 2.1 && phase < 2.42 ? Math.sin((phase - 2.1) / .32 * Math.PI) ** 1.2 : 0;
  return { blink, smile: (1 - Math.cos(time * Math.PI / 4)) * .5 };
}

export function portraitSourcePoint(x: number, y: number, blink: number, smile: number): [number, number] {
  let sourceY = y;
  for (const eye of eyes) {
    const horizontal = 1 - smooth((Math.abs(x - eye.x) / eye.rx - .55) / .45);
    const amount = clamp01(blink) * horizontal;
    if (!amount) continue;
    const dy = y - eye.y, distance = Math.abs(dy), outer = eye.ry + 9;
    if (distance >= outer) continue;
    const closed = eye.ry * (1 - amount * .97);
    // Compress the existing eye; surrounding skin fills the closing eyelid area.
    const mapped = distance < closed ? distance * eye.ry / closed
      : eye.ry + (distance - closed) * (outer - eye.ry) / (outer - closed);
    sourceY += Math.sign(dy) * (mapped - distance);
  }
  // Lift just the two corners of the existing smile, keeping nose and jaw fixed.
  for (const corner of [403, 465]) {
    const dx = (x - corner) / 20, dy = (y - 285) / 23;
    const falloff = Math.max(0, 1 - dx * dx - dy * dy);
    sourceY += clamp01(smile) * 2.8 * falloff * falloff;
  }
  return [x, sourceY];
}

export function createOfficerExpression(image: HTMLImageElement, parent: HTMLElement) {
  const canvas = document.createElement('canvas');
  canvas.className = 'officer-expression';
  canvas.width = FACE.width; canvas.height = FACE.height;
  canvas.setAttribute('aria-hidden', 'true');
  canvas.style.left = `${FACE.x / 860 * 100}%`;
  canvas.style.top = `${FACE.y / 1828 * 100}%`;
  canvas.style.width = `${FACE.width / 860 * 100}%`;
  canvas.style.height = `${FACE.height / 1828 * 100}%`;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) return null;
  context.drawImage(image, FACE.x, FACE.y, FACE.width, FACE.height, 0, 0, FACE.width, FACE.height);
  const source = context.getImageData(0, 0, FACE.width, FACE.height);
  const output = context.createImageData(FACE.width, FACE.height);
  parent.appendChild(canvas);
  let previousBlink = -1, previousSmile = -1;
  return {
    render(time: number, animated: boolean) {
      canvas.hidden = !animated;
      if (!animated) { previousBlink = previousSmile = -1; return; }
      const { blink, smile } = expressionAt(time, animated);
      if (Math.abs(blink - previousBlink) < .002 && Math.abs(smile - previousSmile) < .002) return;
      previousBlink = blink; previousSmile = smile;
      for (let y = 0; y < FACE.height; y++) for (let x = 0; x < FACE.width; x++) {
        const [, sy] = portraitSourcePoint(x + FACE.x, y + FACE.y, blink, smile);
        const row = Math.max(0, Math.min(FACE.height - 1, sy - FACE.y));
        const top = Math.floor(row), bottom = Math.min(top + 1, FACE.height - 1), weight = row - top;
        const offset = (y * FACE.width + x) * 4;
        for (let channel = 0; channel < 4; channel++) {
          output.data[offset + channel] = source.data[(top * FACE.width + x) * 4 + channel] * (1 - weight)
            + source.data[(bottom * FACE.width + x) * 4 + channel] * weight;
        }
      }
      context.putImageData(output, 0, 0);
      // At closure, retain a fine warm eyelid contour instead of a compressed white line.
      if (blink > .75) {
        context.save();
        context.globalAlpha = smooth((blink - .75) / .25) * .8;
        context.strokeStyle = '#81513c'; context.lineWidth = 1.35; context.lineCap = 'round';
        for (const eye of eyes) {
          const cx = eye.x - FACE.x, cy = eye.y - FACE.y, half = eye.rx * .68;
          context.beginPath(); context.moveTo(cx - half, cy);
          context.quadraticCurveTo(cx, cy + 1.4, cx + half, cy);
          context.stroke();
        }
        context.restore();
      }
    },
    dispose() { canvas.remove(); },
  };
}
