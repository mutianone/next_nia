import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { Node, NodeChange } from '@xyflow/react';
import { RelationStudio } from './RelationStudio';
import type { Person } from '../../people-types';
import type { Relation } from '../../pages/Profiles';
vi.mock('@xyflow/react', async importOriginal => {
  const actual = await importOriginal<typeof import('@xyflow/react')>();
  return { ...actual, Background: () => null, Controls: () => null, MiniMap: () => null,
    ReactFlow: ({ nodes, onNodesChange, onNodeClick }: { nodes: Node[]; onNodesChange: (changes: NodeChange[]) => void; onNodeClick: (event: unknown, node: Node) => void }) => <div>{nodes.map(node => <div key={node.id}><button onClick={() => onNodeClick(null, node)}>{node.ariaLabel}</button><button aria-label={'拖动 ' + node.id} onClick={() => onNodesChange([{ id: node.id, type: 'position', position: { x: 125, y: 80 } }])}>拖动</button><output data-testid={'position-' + node.id}>{node.position.x},{node.position.y}</output></div>)}</div>,
  };
});
const person = { id: 'p1', name: '甲', code: 'P001', source: '登记表1', purpose: '学习' } as Person;
const relation = { id: 'r1', person_id: 'p1', target_id: 'p2', label: '同校学习', source: '联系材料2', verified: true, review_note: '登记表已核对', archived: false, revision: 1, other: { id: 'p2', name: '乙', code: 'P002', archived: false } } as Relation;
it('moves a node locally, exposes its evidence, and navigates only from the explicit open button', async () => {
  const onOpen = vi.fn();
  render(<RelationStudio person={person} relations={[relation]} onOpen={onOpen}/>);
  const initial = screen.getByTestId('position-p2').textContent;
  await userEvent.click(screen.getByRole('button', { name: '拖动 p2' }));
  expect(screen.getByTestId('position-p2')).toHaveTextContent('125,80');
  expect(onOpen).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: '关系节点 乙' }));
  const inspector = within(screen.getByLabelText('选中节点详情'));
  expect(inspector.getByText('联系材料2')).toBeInTheDocument();
  expect(inspector.getByText('登记表已核对')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '打开关联档案' }));
  expect(onOpen).toHaveBeenCalledExactlyOnceWith('p2');
  await userEvent.click(screen.getByRole('button', { name: '重置图谱布局' }));
  expect(screen.getByTestId('position-p2').textContent).toBe(initial);
  expect(relation.verified).toBe(true);
});
it('keeps pending and archived relationships out of the default graph and labels pending evidence when enabled', async () => {
  render(<RelationStudio person={person} relations={[{ ...relation, verified: false, review_note: '' }, { ...relation, id: 'r3', archived: true, other: { ...relation.other, id: 'p3', name: '丙' } }]} onOpen={() => {}}/>);
  expect(screen.queryByRole('button', { name: '关系节点 乙' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByLabelText('显示待核验'));
  await userEvent.click(screen.getByRole('button', { name: '关系节点 乙' }));
  expect(within(screen.getByLabelText('选中节点详情')).getByText('待核验')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '关系节点 丙' })).not.toBeInTheDocument();
});
