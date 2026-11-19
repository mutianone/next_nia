"""Extract provisional template answers independently from each engine's full clip."""
from collections import defaultdict
import re
from .record_quality import mixed_answer


def surface(text):
    # Keep decimal points, signs, separators inside numbers and all lexical content.
    return re.sub(r'[\s，。！？!?]+', lambda match: match.group() if (match.start() and match.end() < len(text)
                  and text[match.start()-1].isdigit() and text[match.end()].isdigit()) else '', text)


# These instructions carry no answer or factual qualification. Strip only a
# complete leading sequence, never an arbitrary prefix of a matching question.
_QUESTION_INSTRUCTION = re.compile(
    r'^(?:(?:下面|现在)(?:核实|确认|询问)(?:一项情况|一下|几个问题)|'
    r'请(?:你|您)?(?:听清楚|听好|如实回答|如实作答|认真听|认真回答))'
    r'[，,：:。！!；;\s]*'
)


def question_key(text):
    remaining = text
    while match := _QUESTION_INSTRUCTION.match(remaining):
        remaining = remaining[match.end():]
    return surface(remaining)


def engine_clauses(groups, engine):
    # ASR may split a single question at a voice boundary, even inside a word.
    # Join adjacent original spans before finding punctuation boundaries, while
    # retaining every source ID and each engine's independent acoustic identity.
    text = ''
    spans = []
    for sid, group in groups:
        candidate = (group.get('local_verification') if engine == 'local' else
                     next((c for c in group['candidates'] if c['provider'] == engine), None))
        if not candidate or group.get('errors'):
            text += '\0'
            continue
        for segment in candidate['segments']:
            value = segment['text']
            voice = (segment.get('recognition_run_id'), segment.get('channel_id'), segment['speaker_id']) if segment.get('speaker_id') is not None else None
            spans.append((len(text), len(text) + len(value), sid, voice))
            text += value
    for match in re.finditer(r'[^。！？!?\n\0]+[。！？!?]*|\0', text):
        if match.group() == '\0':
            yield None, [], None
            continue
        clause = match.group().strip()
        if not clause:
            continue
        evidence = [s for s in spans if s[0] < match.end() and s[1] > match.start()]
        voices = {s[3] for s in evidence}
        voice = next(iter(voices)) if len(voices) == 1 else None
        yield clause, list(dict.fromkeys(s[2] for s in evidence)), voice


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

    for clause, ids, voice in engine_clauses(groups, engine):
        if clause is None:
            field = None
            continue
        key = question_key(clause)
        if key in aliases:
            finish()
            targets = aliases[key]
            field = next(iter(targets)) if len(targets) == 1 else None
            if field:
                occurrences[field] += 1
            question, asker = clause, voice
            reply, sources, voices = [], list(ids), []
        elif field:
            if voices and asker is not None and voice == asker and voices[-1] != asker and clause.endswith(('？', '?')):
                finish()
                field = None
            elif mixed_answer(question, clause):
                field = None
            else:
                reply.append(clause)
                voices.append(voice)
                sources.extend(sid for sid in ids if sid not in sources)
    finish()
    return {key: entries for key, entries in found.items() if occurrences[key] == 1}


def prefill(template, groups):
    aliases = defaultdict(set)
    for field in template.get('fields', []):
        if field['id'].startswith('q') and field.get('fillable', True) and not field.get('protected'):
            for label in [field['label'], *field.get('question_aliases', []), *field.get('match_rules', [])]:
                aliases[surface(label)].add(field['id'])
    engines = [answers(groups, engine, aliases) for engine in ('siliconflow', 'volcengine', 'local')]
    values = dict(template.get('values', {}))
    sources = dict(template.get('sources', {}))
    added = {}
    for field in set().union(*(engine.keys() for engine in engines)):
        if field in template.get('structured_fill_sources', {}) or values.get(field) or field in template.get('locked_fields', []):
            continue
        entries = [engine[field][0] for engine in engines if len(engine.get(field, [])) == 1]
        supported = []
        for index, a in enumerate(entries):
            for b in entries[index + 1:]:
                if surface(a[0]) and surface(a[0]) == surface(b[0]) and (a[2] or b[2]) and a[1] == b[1]:
                    supported.append(a)
        if not supported or len({surface(entry[0]) for entry in supported}) != 1:
            continue
        answer = supported[0]
        values[field] = answer[0]
        sources[field] = answer[1]
        added[field] = answer[0]
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
