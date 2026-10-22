import type { Relation } from '../../pages/Profiles';
export type GraphLayout = 'orbit' | 'columns';
export function groupRelations(relations: Relation[], includePending: boolean) {
  const groups = new Map<string, Relation[]>();
  for (const relation of relations) {
    if (relation.archived || relation.other.archived || (!includePending && !relation.verified)) continue;
    groups.set(relation.other.id, [...(groups.get(relation.other.id) || []), relation]);
  }
  return [...groups.entries()];
}
export function graphPosition(index: number, count: number, layout: GraphLayout) {
  if (layout === 'columns') return { x: index % 2 ? 340 : -340, y: (Math.floor(index / 2) - (Math.ceil(count / 2) - 1) / 2) * 140 };
  // Growing circumference keeps cards separated even with large direct networks.
  const radius = Math.max(350, count * 50), angle = index / Math.max(1, count) * Math.PI * 2 - Math.PI / 6;
  return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius * .8 };
}
