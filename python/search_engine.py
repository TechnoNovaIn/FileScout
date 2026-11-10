import re
import unicodedata
from processors.language_processor import normalize_unicode, has_indic_script


def build_search_pattern(keyword, language=None):
    keyword = normalize_unicode(keyword)

    patterns = [re.escape(keyword)]

    if has_indic_script(keyword):
        patterns.append(re.escape(keyword))
    else:
        patterns.append(re.escape(keyword))

    if language and language != 'en':
        try:
            from unidecode import unidecode
            ascii_form = unidecode(keyword)
            if ascii_form and ascii_form != keyword:
                patterns.append(re.escape(ascii_form))
        except ImportError:
            pass

    combined = '|'.join(f'({p})' for p in patterns)
    try:
        return re.compile(combined, re.UNICODE | re.IGNORECASE)
    except Exception:
        return re.compile(re.escape(keyword), re.UNICODE | re.IGNORECASE)


def find_matches_multilingual(text, keyword, context_chars, language=None):
    text = normalize_unicode(text)
    keyword = normalize_unicode(keyword)

    pattern = build_search_pattern(keyword, language)
    matches = []

    for m in pattern.finditer(text):
        start = max(0, m.start() - context_chars)
        end   = min(len(text), m.end() + context_chars)
        snippet = text[start:end].replace("\n", " ").strip()

        match_text = m.group()
        for g in m.groups():
            if g is not None:
                match_text = g
                break

        matches.append({
            "snippet": snippet,
            "match_start": m.start() - start,
            "match_end":   m.end()   - start,
            "match_text":  match_text,
        })

    return matches


def find_matches(text, keyword, context_chars):
    return find_matches_multilingual(text, keyword, context_chars, language=None)
