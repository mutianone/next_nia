import * as T from 'three';
import { expect, it, vi } from 'vitest';
import { buildOfficerModel } from './officerModel';

it('builds a volumetric full-body figure with finite geometry and releases all resources', () => {
  // Canvas badges are visually verified in WebGL; this test inspects actual mesh topology.
  const context = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  const model = buildOfficerModel();
  const bounds = new T.Box3().setFromObject(model.root), size = bounds.getSize(new T.Vector3());
  expect(size.y).toBeGreaterThan(5);expect(size.y).toBeLessThan(6);
  expect(size.z).toBeGreaterThan(.5);
  const geometries = new Set<T.BufferGeometry>(), materials = new Set<T.Material>();
  model.root.traverse(object => { if (object instanceof T.Mesh) {
    geometries.add(object.geometry);(Array.isArray(object.material) ? object.material : [object.material]).forEach(m => materials.add(m));
    const positions=object.geometry.getAttribute('position');
    expect(Array.from(positions.array).every(Number.isFinite)).toBe(true);
  } });
  const disposed = vi.fn(); geometries.forEach(g=>g.addEventListener('dispose',disposed)); materials.forEach(m=>m.addEventListener('dispose',disposed));
  model.animate(.17,true);expect(model.eyes[0].scale.y).toBeLessThan(.2);
  model.animate(2,true);expect(model.eyes[0].scale.y).toBe(1);expect(model.head.rotation.y).not.toBe(0);
  model.animate(2,false);expect(model.eyes[0].scale.y).toBe(1);expect(model.head.rotation.y).toBe(0);expect(model.body.scale.y).toBe(1);
  model.dispose();expect(disposed).toHaveBeenCalledTimes(geometries.size+materials.size);
  context.mockRestore();
});
