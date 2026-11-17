"""Align cloud text to acoustic evidence while preserving every original character."""
from dataclasses import dataclass
from itertools import groupby
import re

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


def reconcile(pieces, chars):
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
    for match in _SHORT_REPLY.finditer(chars):
        at = match.start('question')
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


def aligned_segments(text, tokens, decisions):
    from rapidfuzz.distance import Levenshtein

    cloud = lexical(text)
    local = [(char, index) for index, (token, _, _) in enumerate(tokens) for _, char in lexical(token)]
    aligned, mismatches, missing_words = {}, set(), set()
    for tag, a, b, c, d in Levenshtein.opcodes([ch for _, ch in cloud], [ch for ch, _ in local]):
        if tag == 'equal':
            for offset in range(b-a):
                aligned[a+offset] = local[c+offset][1]
        else:
            # This includes words present only in local ASR, e.g. a missing negative.
            mismatches.update(range(max(0,a-1), min(len(cloud),b+1)))
            if tag == 'insert':
                missing_words.add(a)
    pieces = []
    for index, (pos, _) in enumerate(cloud):
        token = aligned.get(index)
        voice, disputed = decisions[token] if token is not None else (None, True)
        start, end = tokens[token][1:] if token is not None else (None,None)
        stop = cloud[index+1][0] if index+1 < len(cloud) else len(text)
        pieces.append(Piece(text[pos if index else 0:stop], voice, start, end, disputed or index in mismatches))
    reconcile(pieces,''.join(ch for _,ch in cloud))
    segments, last_end = [], 0
    breaks = [0]
    for i in range(1,len(pieces)):
        prev, current = pieces[i-1], pieces[i]
        gap = current.start-prev.end if current.start is not None and prev.end is not None else 0
        if (prev.voice != current.voice or i in missing_words or gap >= 800 or
                prev.text.rstrip().endswith(('。','？','！','?','!'))):
            breaks.append(i)
    breaks.append(len(pieces))
    for a, b in zip(breaks, breaks[1:]):
        items = pieces[a:b]
        if not items:
            continue
        voice = items[0].voice
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
        last_end = segments[-1]['end_ms'] or last_end
    return segments
