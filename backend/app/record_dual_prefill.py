"""Extract provisional template answers independently from each engine's full clip."""
from collections import defaultdict
import re
from .record_quality import mixed_answer


def surface(text):
    # Keep decimal points, signs, separators inside numbers and all lexical content.
    return re.sub(r'[\s，。！？!?]+', lambda match: match.group() if (match.start() and match.end() < len(text)
                  and text[match.start()-1].isdigit() and text[match.end()].isdigit()) else '', text)


def answers(groups, engine, aliases):
    found = defaultdict(list)
    occurrences = defaultdict(int)
    field = None
    question = ''
    reply = []
    sources = []
    voices = []
    asker = None

    def finish():
        if field:
            text = '\n'.join(reply)
            separate = bool(voices and asker is not None and voices[0] is not None
                            and len(set(voices)) == 1 and voices[0] != asker)
            found[field].append((text, sources.copy(), separate))

    for sid, group in groups:
        candidate = next((c for c in group['candidates'] if c['provider'] == engine), None)
        if not candidate or group.get('errors'):
            # A missing span may contain the rest of the preceding answer.
            field = None
            continue
        for segment in candidate['segments']:
            voice = (segment.get('channel_id'), segment['speaker_id']) if segment.get('speaker_id') is not None else None
            for clause in re.findall(r'[^。！？!?\n]+[。！？!?]*', segment['text']):
                clause = clause.strip()
                if not clause:
                    continue
                key = surface(clause)
                if key in aliases:
                    finish()
                    ids = aliases[key]
                    field = next(iter(ids)) if len(ids) == 1 else None
                    if field:
                        occurrences[field] += 1
                    question, asker = clause, voice
                    reply, sources, voices = [], [sid], []
                elif field:
                    if voices and asker is not None and voice == asker and voices[-1] != asker and clause.endswith(('？', '?')):
                        finish()
                        field = None
                    elif mixed_answer(question, clause) or (voices and voice != voices[-1]):
                        # Ambiguous prompt or speaker switch: do not save a prefix.
                        field = None
                    else:
                        reply.append(clause)
                        voices.append(voice)
                        if sid not in sources:
                            sources.append(sid)
    finish()
    return {key: entries for key, entries in found.items() if occurrences[key] == 1}


def prefill(template, groups):
    aliases = defaultdict(set)
    for field in template.get('fields', []):
        if field['id'].startswith('q') and field.get('fillable', True) and not field.get('protected'):
            for label in [field['label'], *field.get('question_aliases', []), *field.get('match_rules', [])]:
                aliases[surface(label)].add(field['id'])
    left, right = [answers(groups, engine, aliases) for engine in ('siliconflow', 'volcengine')]
    values = dict(template.get('values', {}))
    sources = dict(template.get('sources', {}))
    added = {}
    for field in left.keys() & right.keys():
        if field in template.get('structured_fill_sources', {}) or values.get(field) or field in template.get('locked_fields', []) or len(left[field]) != 1 or len(right[field]) != 1:
            continue
        a, b = left[field][0], right[field][0]
        if not surface(a[0]) or surface(a[0]) != surface(b[0]) or not (a[2] or b[2]) or a[1] != b[1]:
            continue
        values[field] = a[0]
        sources[field] = a[1]
        added[field] = a[0]
    if not added:
        return template, []
    return {**template, 'values': values, 'sources': sources,
            'dual_prefill': {**template.get('dual_prefill', {}), **added}}, sorted(added)


def retire_prefill(template, sentence_ids):
    pending = dict(template.get('dual_prefill', {}))
    values = dict(template.get('values', {}))
    sources = dict(template.get('sources', {}))
    for field, original in list(pending.items()):
        if sentence_ids.intersection(sources.get(field, [])):
            del pending[field]
            if field not in template.get('locked_fields', []) and values.get(field) == original:
                values.pop(field, None)
                sources.pop(field, None)
    return {**template, 'values': values, 'sources': sources, 'dual_prefill': pending}
