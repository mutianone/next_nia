export function SpeakerCount({value, onChange, disabled}: {
  value: number | null; onChange: (count: number | null) => void; disabled?: boolean;
}) {
  return <label className="field"><span>预计发言人数</span>
    <select className="select" value={value ?? ''} disabled={disabled}
      onChange={e => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">使用语音服务默认设置</option>
      {Array.from({length: 99}, (_, i) => i + 2).map(n =>
        <option key={n} value={n}>{n} 人{n === 3 ? '（如两位民警和一位群众）' : ''}</option>)}
    </select>
    <small>包括所有实际发言的人。声音编号可在转写后回听并确认角色。</small>
  </label>;
}
