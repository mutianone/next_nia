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


def pair_windows(audio):
    """Partition at sustained quiet valleys without trimming any original samples."""
    import numpy as np
    frame = RATE // 50
    limit = 29 * RATE  # leave room for decoder padding under the 30 s limit
    quiet_blocks = []
    for start in range(0, len(audio), limit):
        block = audio[start:start + limit]
        frames = block[:len(block) // frame * frame].reshape(-1, frame)
        if len(frames):
            rms = np.sqrt(np.mean(frames * frames, axis=1))
            floor = np.clip(np.percentile(rms, 20) * 1.9, .00045, .009)
            quiet_blocks.append(rms < floor)
    quiet = np.concatenate(quiet_blocks) if quiet_blocks else np.array([], dtype=bool)
    edges = np.flatnonzero(np.diff(quiet, prepend=False, append=False))
    pauses = [(a + b) // 2 * frame for a, b in edges.reshape(-1, 2)
              if b - a >= 22 and a >= 8 and b < len(quiet) - 8]
    start = 0
    for end in [*pauses, len(audio)]:
        while end - start > limit:
            yield start, start + limit
            start += limit
        if end > start:
            yield start, end
            start = end


def pair_tokens(recognizer, audio):
    """Keep contextual decoding for long speech and re-listen to isolated short turns."""
    import numpy as np
    tokens, windows = [], []
    pad = RATE // 10
    context = local_tokens(recognizer, audio)
    cursor = 0
    for start, end in pair_windows(audio):
        first_context = cursor
        while cursor < len(context) and context[cursor][1] * RATE < end * 1000:
            cursor += 1
        contextual = context[first_context:cursor]
        if end - start > 3.3 * RATE or sum(len(t) for t, _, _ in contextual) > 8:
            tokens.extend(contextual)
            continue
        part = audio[start:end]
        peak = float(np.max(np.abs(part)))
        if peak < .001:
            tokens.extend(contextual)
            continue
        normalized = part * min(10., .16 / peak)
        padded = np.pad(normalized, (pad, RATE * 15 // 100))
        short = []
        for text, onset, stop in local_tokens(recognizer, padded):
            left = start + max(0, onset * RATE // 1000 - pad)
            right = min(end, start + stop * RATE // 1000 - pad)
            if right - left >= RATE // 1000:
                short.append((text, int(left * 1000 // RATE), int(right * 1000 // RATE)))
        if short and short[-1][2] - short[0][1] <= 1500 and sum(len(t) for t, _, _ in short) <= 6:
            first = len(tokens)
            tokens.extend(short)
            windows.append((first, len(tokens)))
        else:
            tokens.extend(contextual)
    return tokens, windows


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


def paused_voice_decisions(extractor, audio, words):
    """Balance two-speaker clustering by speech phrases instead of pause bins."""
    import numpy as np
    phrases = []
    for index, (_, start, end) in enumerate(words):
        if not 0 <= start < end <= len(audio) * 1000 / RATE + 1:
            return None
        if phrases and start - words[phrases[-1][-1]][2] < 500:
            phrases[-1].append(index)
        else:
            phrases.append([index])
    # Keep inference bounded and retain word-level decisions for long phrases.
    if len(phrases) > MAX_ANCHORS or any(words[p[-1]][2] - words[p[0]][1] > 6000 for p in phrases):
        return None
    vectors, usable = [], []
    for indices in phrases:
        start, end = words[indices[0]][1], words[indices[-1]][2]
        clip = audio[max(0, (start - 80) * 16):min(len(audio), (end + 120) * 16)]
        if len(clip) < RATE // 4 or np.sqrt(np.mean(clip * clip)) < .0005:
            vectors.append(None)
            continue
        stream = extractor.create_stream()
        stream.accept_waveform(RATE, clip)
        stream.input_finished()
        if not extractor.is_ready(stream):
            vectors.append(None)
            continue
        vector = np.asarray(extractor.compute(stream), dtype='float32')
        norm = np.linalg.norm(vector)
        if not np.isfinite(norm) or norm == 0:
            vectors.append(None)
            continue
        vectors.append(vector / norm)
        # An isolated filler or decoder artifact must not occupy a voice cluster.
        if len(indices) >= 2 and end - start >= 300:
            usable.append(len(vectors) - 1)
    if len(usable) < 2:
        return None
    matrix = np.stack([vectors[i] for i in usable])
    groups = cluster_anchors(matrix, 2)
    centers = np.stack([matrix[g].mean(axis=0) for g in groups])
    centers /= np.maximum(np.linalg.norm(centers, axis=1, keepdims=True), 1e-8)
    decisions = [(None, True)] * len(words)
    for indices, vector in zip(phrases, vectors):
        if vector is None:
            continue
        scores = centers @ vector
        label = int(scores.argmax())
        other = max((value for i, value in enumerate(scores) if i != label), default=-1.)
        uncertain = scores[label] < .45 or scores[label] - other < .08
        for index in indices:
            decisions[index] = (None if uncertain else label + 1, bool(uncertain))
    return decisions


def voice_decisions(extractor, audio, words, count, *, anchor_on_words=False):
    import numpy as np
    if not words:
        return []
    if count == 2:
        paused = paused_voice_decisions(extractor, audio, words)
        if paused is not None:
            return paused
    bins = (len(audio)+HOP-1)//HOP
    if anchor_on_words:
        # Fit the two reference voices on timed speech. Background during a
        # long pause can otherwise take one of the two cluster slots.
        speech_bins = np.unique([min(bins-1, (start + min(80, (end-start)//2)) // 100)
                                 for _, start, end in words if 0 <= start < end <= len(audio)*1000/RATE+1])
        anchors = speech_bins[np.linspace(0, len(speech_bins)-1, min(len(speech_bins), MAX_ANCHORS), dtype=int)]
    else:
        anchors = np.unique(np.linspace(0, bins-1, min(bins, MAX_ANCHORS), dtype=int))
    def embedding(index):
        middle = index*HOP+HOP//2
        clip = audio[max(0,middle-4*HOP):middle+4*HOP]
        # Silence provides no speaker evidence.
        if len(clip)<RATE//4:
            return None
        rms = np.sqrt(np.mean(clip*clip))
        if rms < (.0005 if count == 2 else .002):
            return None
        if count == 2:
            clip = clip * min(10., .05 / rms)
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
                windows = []
                if count == 2:
                    words, windows = pair_tokens(recognizer, audio)
                else:
                    words = local_tokens(recognizer,audio)
                labels = voice_decisions(extractor, audio, words, count, anchor_on_words=count == 2)
                segments = aligned_segments(output['text'],words,labels,windows=windows,paired_dialogue=count == 2)
                if not segments:
                    segments = [{'speaker':'unknown','text':output['text'],'voice_review':True}]
                output.setdefault('raw_result', {'text':output['text']})
                output['local_verification'] = {'text': ''.join(w[0] for w in words),
                    'words': words, 'decisions': labels}
                output['segments'] = segments
            return output
    except (OSError, RuntimeError, ValueError, ImportError, subprocess.SubprocessError) as error:
        raise RecordAIError(422,'本机单麦克风分人失败，请检查模型安装和音频格式后重试。') from error
