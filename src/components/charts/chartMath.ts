export type TrendDatum = { label: string; entries: number; exits: number };
export type SeriesKey = 'entries' | 'exits' | 'net';
export const seriesValue = (row: TrendDatum, key: SeriesKey) => key === 'net' ? row.entries - row.exits : row[key];
export function chartScale(data: TrendDatum[], visible: SeriesKey[]) {
  const values = data.flatMap(row => visible.map(key => seriesValue(row, key)));
  const low = Math.min(0, ...values), high = Math.max(0, ...values);
  const raw = Math.max(1, (high - low) / 4), magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map(n => n * magnitude).find(n => n >= raw)!;
  const min = Math.floor(low / step) * step, max = Math.max(step, Math.ceil(high / step) * step);
  return { min, max, ticks: Array.from({ length: Math.round((max - min) / step) + 1 }, (_, i) => min + i * step) };
}
// Equal angles: area (r²) encodes category count, rather than exaggerating it by squaring counts.
export const roseRadius = (value: number, max: number, radius: number) => max > 0 ? Math.sqrt(value / max) * radius : 0;
export function sectorPath(start: number, angle: number, radius: number) {
  const point = (a: number) => `${150 + radius * Math.cos(a)} ${150 + radius * Math.sin(a)}`;
  if (angle >= Math.PI * 2 - 1e-8) return `M 150 ${150-radius} A ${radius} ${radius} 0 1 1 150 ${150+radius} A ${radius} ${radius} 0 1 1 150 ${150-radius} Z`;
  return `M 150 150 L ${point(start)} A ${radius} ${radius} 0 ${angle > Math.PI ? 1 : 0} 1 ${point(start + angle)} Z`;
}
