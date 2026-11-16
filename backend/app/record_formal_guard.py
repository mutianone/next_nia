"""Keep reviewed structured fields out of automatic legacy replacement."""
from copy import deepcopy
from .record_templates import filled_template as legacy_fill


def filled_template(template, facts, source_hash):
    protected = set(template.get('structured_fill_sources', {}))
    result = legacy_fill(template, facts, source_hash)
    for field in protected:
        if field in template.get('values', {}):
            result['values'][field] = template['values'][field]
        else:
            result['values'].pop(field, None)
        result['sources'].pop(field, None)
    if protected and template.get('source_hash') != source_hash:
        # Preserve evidence freshness: old fields are retained, but export stays blocked.
        result['source_hash'] = template.get('source_hash')
    result['structured_fill_sources'] = deepcopy(template.get('structured_fill_sources', {}))
    return result
