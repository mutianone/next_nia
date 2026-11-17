import type { LocalSpeechHint } from '../record-types';

export function LocalSpeechHints({hints, listen}: {hints?: LocalSpeechHint[]; listen?: (ms: number) => void}) {
  return hints?.map(hint => <p className="rec-muted" key={hint.start_ms}>
    {hint.speaker_id != null && `声音 ${hint.speaker_id} · `}本机短句候选「{hint.text}」，请对照原音。
    {listen && <button type="button" className="text-button" onClick={() => listen(Math.max(0, hint.start_ms - 400))}>回听候选位置</button>}
  </p>);
}
