import { expect, it } from 'vitest';
import { expressionAt, FACE, portraitSourcePoint } from './officerExpression';

it('keeps the original expression at rest and blinks briefly at a regular interval', () => {
  expect(expressionAt(2.26, false)).toEqual({ blink: 0, smile: 0 });
  expect(expressionAt(2.26, true).blink).toBeCloseTo(1);
  expect(expressionAt(3, true).blink).toBe(0);
  expect(expressionAt(4, true).smile).toBe(1);
  expect(portraitSourcePoint(321, 160, 0, 0)).toEqual([321, 160]);
});

it('limits expression displacement to the face and keeps patch boundaries seamless', () => {
  for (let x = FACE.x; x < FACE.x + FACE.width; x++) {
    for (const y of [FACE.y, FACE.y + FACE.height - 1]) expect(portraitSourcePoint(x, y, 1, 1)).toEqual([x, y]);
  }
  for (let y = FACE.y; y < FACE.y + FACE.height; y++) {
    for (const x of [FACE.x, FACE.x + FACE.width - 1]) expect(portraitSourcePoint(x, y, 1, 1)).toEqual([x, y]);
  }
  expect(portraitSourcePoint(329, 218, 0, 1)[1]).toBeCloseTo(220.8);
  expect(portraitSourcePoint(350, 195, 1, 1)).toEqual([350, 195]);
});
