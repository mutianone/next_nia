import { expect, it } from 'vitest';
import { groupAnswerClaims } from './answerGroups';
it('groups repeated labels without dropping claims or changing citation IDs', () => {
  const groups=groupAnswerClaims([{text:'【要件对照】事实未知',source_ids:['b']},{text:'【初步定性】尚待核实',source_ids:['a']},{text:'初步定性：不能直接认定',source_ids:['c']},{text:'其他原文',source_ids:['d']}]);
  expect(groups.map(g=>g.heading)).toEqual(['初步定性','要件对照','补充说明']);
  expect(groups[0].claims).toEqual([{text:'尚待核实',source_ids:['a']},{text:'不能直接认定',source_ids:['c']}]);
  expect(groups.flatMap(g=>g.claims)).toHaveLength(4);
});
it('leaves unlabelled general answers and in-text labels intact',()=>{
  expect(groupAnswerClaims([{text:'应核对【处罚依据】原文',source_ids:['a']}])).toEqual([{heading:'',claims:[{text:'应核对【处罚依据】原文',source_ids:['a']}]}]);
});
