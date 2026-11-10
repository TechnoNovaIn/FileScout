import re
import unicodedata

_LANG_DETECTOR = None

def _get_detector():
    global _LANG_DETECTOR
    if _LANG_DETECTOR is None:
        try:
            from langdetect import DetectorFactory
            DetectorFactory.seed = 0
            import langdetect
            _LANG_DETECTOR = langdetect
        except Exception:
            return None
    return _LANG_DETECTOR


EASYOCR_LANG_MAP = {
    'en': 'en', 'hi': 'hi', 'kn': 'kn', 'te': 'te',
    'mr': 'mr', 'bn': 'bn',
}


INDIC_SCRIPTS = {
    'hi': 'Devanagari', 'mr': 'Devanagari', 'bn': 'Bengali',
    'gu': 'Gujarati', 'pa': 'Gurmukhi', 'or': 'Oriya',
    'kn': 'Kannada', 'te': 'Telugu', 'ta': 'Tamil', 'ml': 'Malayalam',
    'si': 'Sinhala',
}


def detect_language(text):
    detector = _get_detector()
    if detector is None or not text.strip():
        return 'en', 0.0
    try:
        langs = detector.detect_langs(text[:1000])
        if langs:
            return langs[0].lang, langs[0].prob
        return 'en', 0.0
    except Exception:
        return 'en', 0.0


def normalize_unicode(text):
    return unicodedata.normalize('NFC', text)


def has_indic_script(text):
    ranges = [
        (0x0900, 0x097F, 'Devanagari'),
        (0x0980, 0x09FF, 'Bengali'),
        (0x0A00, 0x0A7F, 'Gurmukhi'),
        (0x0A80, 0x0AFF, 'Gujarati'),
        (0x0B00, 0x0B7F, 'Oriya'),
        (0x0B80, 0x0BFF, 'Tamil'),
        (0x0C00, 0x0C7F, 'Telugu'),
        (0x0C80, 0x0CFF, 'Kannada'),
        (0x0D00, 0x0D7F, 'Malayalam'),
        (0x0D80, 0x0DFF, 'Sinhala'),
    ]
    for cp in map(ord, text):
        for start, end, _ in ranges:
            if start <= cp <= end:
                return True
    return False


def get_script_name(text):
    ranges = [
        (0x0900, 0x097F, 'Devanagari'),
        (0x0980, 0x09FF, 'Bengali'),
        (0x0A00, 0x0A7F, 'Gurmukhi'),
        (0x0A80, 0x0AFF, 'Gujarati'),
        (0x0B00, 0x0B7F, 'Oriya'),
        (0x0B80, 0x0BFF, 'Tamil'),
        (0x0C00, 0x0C7F, 'Telugu'),
        (0x0C80, 0x0CFF, 'Kannada'),
        (0x0D00, 0x0D7F, 'Malayalam'),
        (0x0D80, 0x0DFF, 'Sinhala'),
    ]
    for cp in map(ord, text):
        for start, end, name in ranges:
            if start <= cp <= end:
                return name
    return 'Latin'


TESSERACT_LANG_MAP = {
    'en': 'eng', 'hi': 'hin', 'kn': 'kan', 'te': 'tel',
    'mr': 'mar', 'ta': 'tam',
}


def resolve_ocr_languages(search_language):
    if not search_language or search_language == 'en':
        return ['en']
    if search_language in EASYOCR_LANG_MAP:
        return ['en', EASYOCR_LANG_MAP[search_language]]
    return ['en']


def resolve_tesseract_languages(languages=None):
    """Resolve languages to Tesseract multi-language string, e.g. 'eng+kan'.
    Accepts string, list, set, or None. Always ensures 'eng' is included.
    If 'all' is requested, returns all available regional languages."""
    if not languages:
        return 'eng+kan+hin'
    if languages == 'all':
        return 'eng+kan+hin+tam+tel+mar'

    selected = {'eng'}
    if isinstance(languages, str):
        items = [x.strip() for x in re.split(r'[,+\s]+', languages) if x.strip()]
    elif isinstance(languages, (list, set, tuple)):
        items = list(languages)
    else:
        items = [str(languages)]

    for item in items:
        code = item.lower()
        if code in TESSERACT_LANG_MAP:
            selected.add(TESSERACT_LANG_MAP[code])
        elif code in ('eng', 'kan', 'hin', 'tam', 'tel', 'mar', 'osd'):
            selected.add(code)

    ordered = ['eng'] + [l for l in sorted(selected) if l != 'eng']
    return '+'.join(ordered)

