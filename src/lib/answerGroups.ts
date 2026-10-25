import type { Answer } from '../knowledge-types';
type Claim = NonNullable<Answer['claims']>[number];
const headings = ['初步定性', '要件对照', '待核实事项', '处罚依据', '裁量基准原文'] as const;
/** Group only explicit labels: never infer legal categories or alter claim evidence. */
export function groupAnswerClaims(claims: Claim[]) {
  const groups = new Map<string, Claim[]>();
  for (const claim of claims) {
    const match = claim.text.match(/^\s*(?:【(初步定性|要件对照|待核实事项|处罚依据|裁量基准原文)】\s*[：:]?|(初步定性|要件对照|待核实事项|处罚依据|裁量基准原文)\s*[：:])\s*/);
    const heading = match?.[1] || match?.[2] || '';
    const items = groups.get(heading) || [];
    items.push({ ...claim, text: match ? claim.text.slice(match[0].length) : claim.text });
    groups.set(heading, items);
  }
  return [...headings, ''].filter(heading => groups.has(heading)).map(heading => ({ heading: heading || (groups.size > 1 ? '补充说明' : ''), claims: groups.get(heading)! }));
}
