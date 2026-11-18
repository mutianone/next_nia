export type VolcengineConfig = {
  model: 'volc.seedasr.auc'; tos_bucket: string; tos_region: string;
  api_key_configured: boolean; tos_access_key_configured: boolean; tos_secret_key_configured: boolean;
};
export const defaultVolcengine: VolcengineConfig = {
  model: 'volc.seedasr.auc', tos_bucket: '', tos_region: 'cn-beijing',
  api_key_configured: false, tos_access_key_configured: false, tos_secret_key_configured: false,
};
export const emptyVolcengineSecrets = {
  api_key: '', tos_access_key: '', tos_secret_key: '',
  clear_api_key: false, clear_tos_access_key: false, clear_tos_secret_key: false,
};
export type VolcengineSecrets = typeof emptyVolcengineSecrets;

export function VolcengineSpeechSettings({config, secrets, disabled, onConfig, onSecrets}: {
  config: VolcengineConfig; secrets: VolcengineSecrets; disabled: boolean;
  onConfig: (value: VolcengineConfig) => void; onSecrets: (value: VolcengineSecrets) => void;
}) {
  return <div>
    <p>豆包录音文件识别 2.0。双人及少人数对话使用标准分人模式；超过 5 人使用会议模式。声音编号需要回听确认角色。</p>
    <div className="settings-field-grid">
      {([{key:'api_key', label:'火山语音 API Key'}, {key:'tos_access_key', label:'TOS Access Key ID'},
        {key:'tos_secret_key', label:'TOS Secret Access Key'}] as const).map(({key, label}) => <div key={key}>
        <label className="field">{label}<input className="input" type="password" autoComplete="new-password"
          value={secrets[key]} disabled={disabled || secrets[`clear_${key}`]}
          placeholder={config[`${key}_configured`] ? '已配置，留空保留' : `填写${label}`}
          onChange={e => onSecrets({...secrets, [key]: e.target.value})}/></label>
        {config[`${key}_configured`] && <label><input type="checkbox" disabled={disabled}
          checked={secrets[`clear_${key}`]} onChange={e => onSecrets({...secrets, [key]: '', [`clear_${key}`]: e.target.checked})}/>
          清空{label}</label>}
      </div>)}
      <label className="field">TOS 私有桶名称<input className="input" value={config.tos_bucket} disabled={disabled}
        placeholder="填写已创建的私有桶名称" onChange={e => onConfig({...config, tos_bucket: e.target.value.trim()})}/></label>
      <label className="field">TOS 桶所在地域<input className="input" value={config.tos_region} disabled={disabled}
        placeholder="cn-beijing" onChange={e => onConfig({...config, tos_region: e.target.value.trim()})}/></label>
    </div>
    <p className="rec-muted">火山通过音频链接读取文件，需同时配置语音 Key 和 TOS 上传凭据。转写副本存入私有桶，链接有效期 24 小时；任务完成后尝试删除。请为桶内 next-nia-asr/ 前缀设置 1 天自动过期，清理中断任务的文件。保存后在智能笔录上传录音验证。</p>
  </div>;
}
