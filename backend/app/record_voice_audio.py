"""Optional single-microphone speaker analysis. All model inference stays local."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from functools import lru_cache
from importlib.util import find_spec

from .record_voice_alignment import aligned_segments
from .record_media import DEMUXERS, MAX_SECONDS, conversion_binary

RATE = 16000
HOP = 1600
# Bound both the clustering matrix and CPU usage on long recordings.
MAX_ANCHORS = 384
_lock = threading.Lock()


def model_dir():
    return Path(os.environ.get('CRJ_VOICE_MODEL_DIR', Path(__file__).resolve().parents[1] / 'data' / 'voice-models'))


def available():
    return (bool(conversion_binary()) and all(find_spec(p) is not None for p in ('numpy', 'rapidfuzz', 'sherpa_onnx'))
            and all((model_dir()/name).is_file() for name in ('embedding.onnx', 'model.int8.onnx', 'tokens.txt')))


@lru_cache(maxsize=1)
def models(directory):
    import sherpa_onnx as sherpa
    extractor = sherpa.SpeakerEmbeddingExtractor(sherpa.SpeakerEmbeddingExtractorConfig(model=str(directory/'embedding.onnx')))
    recognizer = sherpa.OfflineRecognizer.from_sense_voice(model=str(directory/'model.int8.onnx'),
        tokens=str(directory/'tokens.txt'), use_itn=True, num_threads=2)
    return extractor, recognizer


def samples(raw, fmt):
    import numpy as np
    # Decode the original once. The length cap also applies to undeclared MP3s.
    with tempfile.TemporaryDirectory(prefix='nia-voice-') as folder:
        path = Path(folder)/'source'
        path.write_bytes(raw)
        cmd = [conversion_binary(), '-nostdin', '-loglevel', 'error', '-xerror', '-protocol_whitelist', 'file,pipe',
               '-f', 'mp3' if fmt == 'mp3' else DEMUXERS[fmt], '-i', str(path), '-map', '0:a:0',
               '-ac', '1', '-ar', str(RATE), '-t', str(MAX_SECONDS+1), '-f', 's16le', 'pipe:1']
        result = subprocess.run(cmd, capture_output=True, timeout=90, check=True)
    if not 0 < len(result.stdout) <= MAX_SECONDS*RATE*2:
        raise ValueError('duration')
    return np.frombuffer(result.stdout, dtype='<i2').astype('float32') / 32768


def local_tokens(recognizer, audio):
    result = []
    # Keep recognizer memory bounded. Each interval has its own original offset.
    for offset in range(0, len(audio), 30*RATE):
        part = audio[offset:offset+30*RATE]
        if len(part) < RATE//10:
            continue
        stream = recognizer.create_stream()
        stream.accept_waveform(RATE, part)
        recognizer.decode_stream(stream)
        decoded = stream.result
        stamps = list(decoded.timestamps)
        if len(stamps) != len(decoded.tokens):
            continue
        for i, (word, start) in enumerate(zip(decoded.tokens, stamps)):
            # CTC supplies onsets. Clip estimated ends to the next onset and the audio.
            end = min(start+0.18, stamps[i+1] if i+1<len(stamps) else len(part)/RATE, len(part)/RATE)
            left, right = round((offset/RATE+start)*1000), round((offset/RATE+end)*1000)
            if left < right:
                result.append((word, left, right))
    return result


def cluster_anchors(matrix, count):
    import numpy as np
    # Average-link clustering of normalized voice embeddings.
    groups = [[i] for i in range(len(matrix))]
    similarity = matrix @ matrix.T
    while len(groups) > 1:
        np.fill_diagonal(similarity, -np.inf)
        a, b = sorted(np.unravel_index(similarity.argmax(), similarity.shape))
        if len(groups) <= count and similarity[a,b] < .84:
            break
        sizes = len(groups[a]), len(groups[b])
        similarity[a,:] = (sizes[0]*similarity[a,:] + sizes[1]*similarity[b,:]) / sum(sizes)
        similarity[:,a] = similarity[a,:]
        similarity = np.delete(np.delete(similarity, b, axis=0), b, axis=1)
        groups[a].extend(groups.pop(b))
    return groups


def voice_decisions(extractor, audio, words, count):
    import numpy as np
    if not words:
        return []
    bins = (len(audio)+HOP-1)//HOP
    anchors = np.unique(np.linspace(0, bins-1, min(bins, MAX_ANCHORS), dtype=int))
    def embedding(index):
        middle = index*HOP+HOP//2
        clip = audio[max(0,middle-4*HOP):middle+4*HOP]
        # Silence provides no speaker evidence.
        if len(clip)<RATE//4 or np.sqrt(np.mean(clip*clip)) < .002:
            return None
        stream = extractor.create_stream()
        stream.accept_waveform(RATE, clip)
        stream.input_finished()
        if not extractor.is_ready(stream):
            return None
        vec = np.asarray(extractor.compute(stream), dtype='float32')
        norm = np.linalg.norm(vec)
        return vec/norm if np.isfinite(norm) and norm>0 else None
    vectors = {int(i):v for i in anchors if (v:=embedding(i)) is not None}
    if not vectors:
        return [(None,True)]*len(words)
    indices = list(vectors)
    matrix = np.stack(list(vectors.values()))
    groups = cluster_anchors(matrix, count)
    centers = np.stack([matrix[g].mean(axis=0) for g in groups])
    centers /= np.maximum(np.linalg.norm(centers, axis=1, keepdims=True), 1e-8)
    anchors_by_id = {indices[i]:label for label,g in enumerate(groups) for i in g}
    decisions = {}
    def classify(i):
        if i not in decisions:
            vec = vectors.get(i) if i in vectors else embedding(i)
            if vec is None:
                decisions[i] = (None, True)
            else:
                scores = centers @ vec
                label = anchors_by_id.get(i, int(scores.argmax()))
                other = max((n for j,n in enumerate(scores) if label!=j), default=-1.)
                decisions[i] = (label+1, scores[label]<.45 or scores[label]-other<.08)
        return decisions[i]
    result = []
    for _, start, end in words:
        if start < 0 or not start < end <= len(audio)*1000/RATE+1:
            result.append((None,True))
            continue
        center = min(bins-1,(start+min(80,(end-start)//2))//100)
        voice, unsure = classify(center)
        # A change close enough to clip a syllable needs listening even when
        # the majority voice is clear. Never use it to confirm a business role.
        neighbors = (max(0,center-2), min(bins-1,center+2), min(bins-1,max(0,(end-1)//100)))
        conflict = any(classify(i)[0] != voice for i in neighbors)
        result.append((voice, bool(unsure or conflict)))
    return result


def annotate(raw, fmt, output, count):
    from .records_ai import RecordAIError
    try:
        with _lock:
            audio = samples(raw, fmt)
            extractor, recognizer = models(model_dir())
            if output.get('provider') == 'aliyun':
                segments = output['segments']
                words = [(w['text'],w['begin_time'],w['end_time']) for s in segments for w in s.get('words') or []
                         if w.get('begin_time') is not None and w.get('end_time') is not None]
                decisions = iter(voice_decisions(extractor,audio,words,count))
                evidence = []
                for segment in segments:
                    labels = [next(decisions) for w in segment.get('words') or [] if w.get('begin_time') is not None and w.get('end_time') is not None]
                    evidence.append({v for v,disputed in labels if v is not None and not disputed})
                    segment['voice_review'] = (not labels or len(labels)!=len(segment.get('words') or []) or
                                               any(d for _,d in labels) or len(evidence[-1])!=1 or segment.get('speaker_id') is None)
                # Provider and local IDs have unrelated names. A many-to-one
                # correspondence in either direction calls both groups into question.
                by_provider, by_local = {}, {}
                for seg, labels in zip(segments,evidence):
                    sp = seg.get('speaker_id')
                    if sp is not None:
                        by_provider.setdefault(sp,set()).update(labels)
                        for v in labels: by_local.setdefault(v,set()).add(sp)
                for seg, labels in zip(segments,evidence):
                    if len(by_provider.get(seg.get('speaker_id'),()))>1 or any(len(by_local.get(v,()))>1 for v in labels):
                        seg['voice_review'] = True
            else:
                words = local_tokens(recognizer,audio)
                labels = voice_decisions(extractor,audio,words,count)
                segments = aligned_segments(output['text'],words,labels)
                if not segments:
                    segments = [{'speaker':'unknown','text':output['text'],'voice_review':True}]
                output.setdefault('raw_result', {'text':output['text']})
                output['segments'] = segments
            return output
    except (OSError, RuntimeError, ValueError, ImportError, subprocess.SubprocessError) as error:
        raise RecordAIError(422,'本机单麦克风分人失败，请检查模型安装和音频格式后重试。') from error
