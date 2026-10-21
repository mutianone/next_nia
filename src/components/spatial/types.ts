export type SpatialVariant = 'dashboard' | 'assistant' | 'knowledge' | 'people' | 'alerts' | 'records' | 'documents' | 'profiles' | 'analytics' | 'appearance' | 'settings';
export type SceneOptions = { animated: boolean; expanded: boolean; accent: string; primary: string };
export type SpatialController = {
  update(options: SceneOptions): void;
  point(x: number, y: number): void;
  turn(x: number, y: number): void;
  reset(): void;
  dispose(): void;
};
