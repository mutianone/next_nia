"""Align cloud text to acoustic evidence while preserving every original character."""
from dataclasses import dataclass
from bisect import bisect_left
from itertools import groupby
import re

from .record_qa_boundaries import question_answer_cuts, restates_question, short_reply_question_cuts
_DIGITS = str.maketrans("〇零一二三四五六七八九", "00123456789")
_SHORT_REPLY = re.compile(r'(?:是的|对的|没有|不是|我确定|我不确定|不知道|不清楚)(?P<question>[你您](?:是|在|有|哪|什|还|记|能|的|当))')


def lexical(text):
    return [(index, ch.lower().translate(_DIGITS)) for index, ch in enumerate(text) if ch.isalnum()]


@dataclass
class Piece:
    text: str
    voice: int | None
    start: int | None
    end: int | None
    review: bool


def reconcile(pieces, chars, dialogue_cuts):
    # Only bridge untimed text if two nearby anchors agree. Its words remain untimed.
    for missing, group in groupby(range(len(pieces)), key=lambda i: pieces[i].start is None):
        indices = list(group)
        if not missing or indices[0] == 0 or indices[-1]+1 == len(pieces):
            continue
        prev, nxt = pieces[indices[0]-1], pieces[indices[-1]+1]
        if prev.voice is not None and prev.voice == nxt.voice and nxt.start-prev.end <= 650:
            for i in indices:
                pieces[i].voice = prev.voice
    # A close acoustic transition can land one character into the next question.
    # Suggest the lexical onset only when voices on the two sides differ.
    transitions = {match.start('question') for match in _SHORT_REPLY.finditer(chars)}
    for at in sorted(transitions | dialogue_cuts):
        first, last = pieces[at-1], pieces[at]
        if first.voice is None or last.start is None or last.voice != first.voice:
            continue
        for i in range(at+1, min(at+3,len(pieces))):
            nxt = pieces[i]
            if nxt.start is None or nxt.start-last.start > 300 or nxt.voice is None:
                break
            if nxt.voice != first.voice:
                for p in pieces[at:i]:
                    p.voice, p.review = nxt.voice, True
                first.review = True
                break


def short_window_evidence(tokens, windows, local, cloud, edits):
    """Map a short, separately decoded phrase to an unambiguous cloud range."""
    token_indices = [i for _, i in local]
    edges = {}
    for tag, a, b, c, d in edits:
        for key, value in ((c, a), (d, b)):
            edges[key] = value if edges.get(key, value) == value else None
        if tag == 'equal':
            edges.update((c + offset, a + offset) for offset in range(1, d - c))
    for first, last in windows:
        left, right = bisect_left(token_indices, first), bisect_left(token_indices, last)
        a, b = edges.get(left), edges.get(right)
        if left == right or a is None or b is None or b - a > 12:
            continue
        suggestion = None
        if [c for _, c in cloud[a:b]] != [c for c, _ in local[left:right]]:
            suggestion = {'text': ''.join(token for token, _, _ in tokens[first:last]),
                          'start_ms': tokens[first][1], 'end_ms': tokens[last-1][2]}
        yield a, b, suggestion, first, last


def aligned_segments(text, tokens, decisions, *, windows=(), paired_dialogue=False):
    from rapidfuzz.distance import Levenshtein

    cloud = lexical(text)
    local = [(char, index) for index, (token, _, _) in enumerate(tokens) for _, char in lexical(token)]
    aligned, mismatches, missing_words = {}, set(), set()
    edits = Levenshtein.opcodes([ch for _, ch in cloud], [ch for ch, _ in local])
    for tag, a, b, c, d in edits:
        if tag == 'equal':
            for offset in range(b-a):
                aligned[a+offset] = local[c+offset][1]
        else:
            # This includes words present only in local ASR, e.g. a missing negative.
            mismatches.update(range(max(0,a-1), min(len(cloud),b+1)))
            if tag == 'insert':
                missing_words.add(a)
    hints, keep_together = {}, set()
    if windows and cloud:
        for a, b, hint, first, last in short_window_evidence(tokens, windows, local, cloud, edits):
            missing_words.update((a, b))
            if hint:
                # A completely omitted reply has no cloud characters of its own.
                # Attach a listening note, never fabricate a transcript segment.
                anchor = min(a, len(cloud) - 1)
                local_voices = decisions[first:last]
                voices = {v for v, _ in local_voices if v is not None}
                if len(local_voices) >= 2 and len(voices) == 1 and all(v is not None and not unsure for v, unsure in local_voices):
                    hint['speaker_id'] = local_voices[0][0]
                hints.setdefault(anchor, []).append(hint)
                mismatches.update(range(anchor, max(anchor + 1, b)))
                # Keep a disputed short reply readable when some syllables
                # disagree. Never fuse an internal sentence or voice change.
                if 0 < b - a <= 8 and len(voices) == 1 and not re.search(r'[。！？?!]', text[cloud[a][0]:cloud[b-1][0]]):
                    keep_together.update(range(a+1, b))
    pieces = []
    for index, (pos, _) in enumerate(cloud):
        token = aligned.get(index)
        voice, disputed = decisions[token] if token is not None else (None, True)
        start, end = tokens[token][1:] if token is not None else (None,None)
        stop = cloud[index+1][0] if index+1 < len(cloud) else len(text)
        pieces.append(Piece(text[pos if index else 0:stop], voice, start, end, disputed or index in mismatches))
    dialogue_cuts = set()
    if paired_dialogue:
        # Text proposes a turn. Require a nearby change of voice or a pause
        # before separating an unpunctuated question from a brief answer.
        positions = [pos for pos, _ in cloud]
        for offset in question_answer_cuts(text) + short_reply_question_cuts(text):
            # "我问的是…" can be the questioner's next sentence. Keep the
            # measured acoustic boundary instead of shifting it onto this 我.
            if restates_question(text[offset:offset+12]):
                continue
            at = bisect_left(positions, offset)
            if 0 < at < len(pieces):
                dialogue_cuts.add(at)
                prev, current = pieces[at-1], pieces[at]
                if (prev.end is not None and current.start is not None and current.start - prev.end >= 180
                        and prev.voice == current.voice and not prev.text.rstrip().endswith(('？','?','。','！','!'))):
                    missing_words.add(at)
                    prev.review = current.review = True
    reconcile(pieces,''.join(ch for _,ch in cloud),dialogue_cuts)
    segments, last_end = [], 0
    breaks = [0]
    for i in range(1,len(pieces)):
        prev, current = pieces[i-1], pieces[i]
        gap = current.start-prev.end if current.start is not None and prev.end is not None else 0
        if i not in keep_together and (prev.voice != current.voice or i in missing_words or gap >= 800 or
                                      prev.text.rstrip().endswith(('。','？','！','?','!'))):
            breaks.append(i)
    breaks.append(len(pieces))
    for a, b in zip(breaks, breaks[1:]):
        items = pieces[a:b]
        if not items:
            continue
        voice = items[0].voice if all(p.voice == items[0].voice for p in items) else None
        words = [{'text':p.text,'begin_time':p.start,'end_time':p.end,'voice_review':p.review}
                 for p in items if p.start is not None]
        start = words[0]['begin_time'] if words else None
        review_word = next((w for w in words if w['voice_review']),None)
        listen_at = review_word['begin_time'] if review_word else start
        segments.append({'speaker':'unknown','speaker_id':voice,'channel_id':0,'word_boundaries':True,
                         'text':''.join(p.text for p in items), 'start_ms':start,
                         'end_ms':words[-1]['end_time'] if words else None, 'words':words,
                         'timestamp_source':'LOCAL_ESTIMATE' if words else 'UNKNOWN',
                         'listen_start_ms':max(0,(listen_at if listen_at is not None else last_end)-400),
                         'voice_review':any(p.review for p in items)})
        alternatives = [hint for i in range(a, b) for hint in hints.get(i, ())]
        if alternatives:
            segments[-1]['local_alternatives'] = alternatives
        last_end = segments[-1]['end_ms'] or last_end
    return segments
