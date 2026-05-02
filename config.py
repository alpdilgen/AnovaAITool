# Configuration for Enhanced Translation Assistant

# OpenAI Models
OPENAI_MODELS = [
    'gpt-4o',
    'gpt-4o-mini',
    'gpt-4-turbo',
]

# Translation settings
DEFAULT_ACCEPTANCE_THRESHOLD = 95      # % - bypass segments at this match or higher
DEFAULT_MATCH_THRESHOLD = 70           # % - use fuzzy match for TM context
DEFAULT_CHAT_HISTORY = 5               # segments to include in history for consistency

# App name
APP_NAME = "Enhanced Translation Assistant"

# Prompt template
PROMPT_TEMPLATE_PATH = None  # Set to file path if using custom template, None for default

# ISO 639-1 to memoQ 3-letter language code mapping
ISO_TO_MEMOQ_LANG = {
    'en': 'eng', 'tr': 'tur', 'de': 'ger', 'fr': 'fre', 'es': 'spa',
    'it': 'ita', 'pt': 'por', 'pl': 'pol', 'ru': 'rus', 'ja': 'jpn',
    'zh': 'zho', 'ar': 'ara', 'ko': 'kor', 'nl': 'dut', 'sv': 'swe',
    'no': 'nor', 'da': 'dan', 'fi': 'fin', 'el': 'gre', 'he': 'heb',
    'th': 'tha', 'vi': 'vie', 'bg': 'bul', 'ro': 'rum', 'cs': 'cze',
    'sk': 'slo', 'uk': 'ukr', 'et': 'est', 'lv': 'lav', 'lt': 'lit',
    'hu': 'hun', 'hr': 'hrv', 'sl': 'slv', 'mt': 'mlt', 'ga': 'gle',
    'af': 'afr', 'bn': 'ben', 'hi': 'hin',
}

# Comprehensive memoQ language code → display name mapping
# Covers both 3-letter (eng, tur) and 2-letter (en, tr) codes with locales
MEMOQ_LANG_NAMES = {
    # English variants
    'eng': 'English', 'en': 'English',
    'eng-us': 'English (United States)', 'en-us': 'English (United States)',
    'eng-gb': 'English (United Kingdom)', 'en-gb': 'English (United Kingdom)',
    'eng-au': 'English (Australia)', 'en-au': 'English (Australia)',
    'eng-ca': 'English (Canada)', 'en-ca': 'English (Canada)',
    'eng-ie': 'English (Ireland)', 'en-ie': 'English (Ireland)',
    'eng-nz': 'English (New Zealand)', 'en-nz': 'English (New Zealand)',
    'eng-za': 'English (South Africa)', 'en-za': 'English (South Africa)',
    'eng-ph': 'English (Philippines)', 'en-ph': 'English (Philippines)',
    'eng-bz': 'English (Belize)', 'en-bz': 'English (Belize)',
    'eng-cb': 'English (Caribbean)', 'en-029': 'English (Caribbean)',
    'eng-jm': 'English (Jamaica)', 'en-jm': 'English (Jamaica)',
    'eng-tt': 'English (Trinidad and Tobago)', 'en-tt': 'English (Trinidad and Tobago)',
    'eng-zw': 'English (Zimbabwe)', 'en-zw': 'English (Zimbabwe)',
    # Turkish
    'tur': 'Turkish', 'tr': 'Turkish',
    # German variants
    'ger': 'German', 'de': 'German',
    'ger-de': 'German (Germany)', 'de-de': 'German (Germany)',
    'ger-at': 'German (Austria)', 'de-at': 'German (Austria)',
    'ger-ch': 'German (Switzerland)', 'de-ch': 'German (Switzerland)',
    'ger-li': 'German (Liechtenstein)', 'de-li': 'German (Liechtenstein)',
    # French variants
    'fre': 'French', 'fr': 'French',
    'fre-fr': 'French (France)', 'fr-fr': 'French (France)',
    'fre-be': 'French (Belgium)', 'fr-be': 'French (Belgium)',
    'fre-ca': 'French (Canada)', 'fr-ca': 'French (Canada)',
    'fre-ch': 'French (Switzerland)', 'fr-ch': 'French (Switzerland)',
    'fre-lu': 'French (Luxembourg)', 'fr-lu': 'French (Luxembourg)',
    'fre-ma': 'French (Morocco)', 'fr-ma': 'French (Morocco)',
    'fre-02': 'French (Africa)', 'fr-002': 'French (Africa)',
    # Spanish variants
    'spa': 'Spanish', 'es': 'Spanish',
    'spa-es': 'Spanish (Spain)', 'es-es': 'Spanish (Spain)',
    'spa-mx': 'Spanish (Mexico)', 'es-mx': 'Spanish (Mexico)',
    'spa-ar': 'Spanish (Argentina)', 'es-ar': 'Spanish (Argentina)',
    'spa-cl': 'Spanish (Chile)', 'es-cl': 'Spanish (Chile)',
    'spa-us': 'Spanish (United States)', 'es-us': 'Spanish (United States)',
    'spa-pe': 'Spanish (Peru)', 'es-pe': 'Spanish (Peru)',
    'spa-ve': 'Spanish (Venezuela)', 'es-ve': 'Spanish (Venezuela)',
    'spa-do': 'Spanish (Dominican Republic)', 'es-do': 'Spanish (Dominican Republic)',
    'spa-uy': 'Spanish (Uruguay)', 'es-uy': 'Spanish (Uruguay)',
    # Portuguese variants
    'por': 'Portuguese', 'pt': 'Portuguese',
    'por-br': 'Portuguese (Brazil)', 'pt-br': 'Portuguese (Brazil)',
    'por-pt': 'Portuguese (Portugal)', 'pt-pt': 'Portuguese (Portugal)',
    # Italian
    'ita': 'Italian', 'it': 'Italian',
    'ita-it': 'Italian (Italy)', 'it-it': 'Italian (Italy)',
    # Dutch variants
    'dut': 'Dutch', 'nl': 'Dutch',
    'dut-nl': 'Dutch (Netherlands)', 'nl-nl': 'Dutch (Netherlands)',
    'dut-be': 'Dutch (Belgium)', 'nl-be': 'Dutch (Belgium)',
    'vls': 'Flemish',
    # Chinese variants
    'zho-cn': 'Chinese (PRC)', 'zh-cn': 'Chinese (PRC)',
    'zho-tw': 'Chinese (Taiwan)', 'zh-tw': 'Chinese (Taiwan)',
    'zho-hk': 'Chinese (Hong Kong)', 'zh-hk': 'Chinese (Hong Kong)',
    'zho-sg': 'Chinese (Singapore)', 'zh-sg': 'Chinese (Singapore)',
    'zho-mo': 'Chinese (Macao)', 'zh-mo': 'Chinese (Macao)',
    'zho': 'Chinese', 'zh': 'Chinese',
    # Japanese, Korean
    'jpn': 'Japanese', 'ja': 'Japanese',
    'kor': 'Korean', 'ko': 'Korean',
    # Arabic variants
    'ara': 'Arabic', 'ar': 'Arabic',
    'ara-sa': 'Arabic (Saudi Arabia)', 'ar-sa': 'Arabic (Saudi Arabia)',
    'ara-eg': 'Arabic (Egypt)', 'ar-eg': 'Arabic (Egypt)',
    'ara-ae': 'Arabic (U.A.E.)', 'ar-ae': 'Arabic (U.A.E.)',
    'ara-dz': 'Arabic (Algeria)', 'ar-dz': 'Arabic (Algeria)',
    'ara-bh': 'Arabic (Bahrain)', 'ar-bh': 'Arabic (Bahrain)',
    'ara-iq': 'Arabic (Iraq)', 'ar-iq': 'Arabic (Iraq)',
    'ara-jo': 'Arabic (Jordan)', 'ar-jo': 'Arabic (Jordan)',
    'ara-kw': 'Arabic (Kuwait)', 'ar-kw': 'Arabic (Kuwait)',
    'ara-lb': 'Arabic (Lebanon)', 'ar-lb': 'Arabic (Lebanon)',
    'ara-ly': 'Arabic (Libya)', 'ar-ly': 'Arabic (Libya)',
    'ara-ma': 'Arabic (Morocco)', 'ar-ma': 'Arabic (Morocco)',
    'ara-om': 'Arabic (Oman)', 'ar-om': 'Arabic (Oman)',
    'ara-qa': 'Arabic (Qatar)', 'ar-qa': 'Arabic (Qatar)',
    'ara-sy': 'Arabic (Syria)', 'ar-sy': 'Arabic (Syria)',
    'ara-tn': 'Arabic (Tunisia)', 'ar-tn': 'Arabic (Tunisia)',
    'ara-ye': 'Arabic (Yemen)', 'ar-ye': 'Arabic (Yemen)',
    # Other European languages
    'rus': 'Russian', 'ru': 'Russian',
    'pol': 'Polish', 'pl': 'Polish',
    'cze': 'Czech', 'cs': 'Czech',
    'slo': 'Slovak', 'sk': 'Slovak',
    'hun': 'Hungarian', 'hu': 'Hungarian',
    'rum': 'Romanian', 'ro': 'Romanian',
    'bul': 'Bulgarian', 'bg': 'Bulgarian',
    'hrv': 'Croatian', 'hr': 'Croatian',
    'slv': 'Slovenian', 'sl': 'Slovenian',
    'swe': 'Swedish', 'sv': 'Swedish',
    'nor': 'Norwegian', 'no': 'Norwegian',
    'nno': 'Norwegian (Nynorsk)', 'nn': 'Norwegian (Nynorsk)',
    'dan': 'Danish', 'da': 'Danish',
    'fin': 'Finnish', 'fi': 'Finnish',
    'est': 'Estonian', 'et': 'Estonian',
    'lav': 'Latvian', 'lv': 'Latvian',
    'lit': 'Lithuanian', 'lt': 'Lithuanian',
    'gre': 'Greek', 'el': 'Greek',
    'heb': 'Hebrew', 'he': 'Hebrew',
    'ukr': 'Ukrainian', 'uk': 'Ukrainian',
    'mlt': 'Maltese', 'mt': 'Maltese',
    'gle': 'Irish', 'ga': 'Irish',
    'alb': 'Albanian', 'sq': 'Albanian',
    'bel': 'Belarussian', 'be': 'Belarussian',
    'bos': 'Bosnian (Latin)', 'bs-latn': 'Bosnian (Latin)',
    'scc': 'Serbian (Cyrillic)', 'sr': 'Serbian (Cyrillic)',
    'scr': 'Serbian (Latin)', 'sh': 'Serbian (Latin)',
    'ice': 'Icelandic', 'is': 'Icelandic',
    'cat': 'Catalan', 'ca': 'Catalan',
    'baq': 'Basque', 'eu': 'Basque',
    'glg': 'Galician', 'gl': 'Galician',
    'wel': 'Welsh', 'cy': 'Welsh',
    'ltz': 'Luxembourgish', 'lb': 'Luxembourgish',
    # Asian languages
    'hin': 'Hindi', 'hi': 'Hindi',
    'ben': 'Bengali', 'bn': 'Bengali',
    'tha': 'Thai', 'th': 'Thai',
    'vie': 'Vietnamese', 'vi': 'Vietnamese',
    'ind': 'Indonesian', 'id': 'Indonesian',
    'msa': 'Malay', 'ms': 'Malay',
    'tgl': 'Tagalog', 'tl': 'Tagalog',
    'fil': 'Filipino',
    'khm': 'Khmer', 'km': 'Khmer',
    'mya': 'Burmese', 'my': 'Burmese',
    'tam': 'Tamil', 'ta': 'Tamil',
    'tel': 'Telugu', 'te': 'Telugu',
    'urd': 'Urdu', 'ur': 'Urdu',
    'fas': 'Farsi', 'fa': 'Farsi',
    'kat': 'Georgian', 'ka': 'Georgian',
    'hye': 'Armenian', 'hy': 'Armenian',
    'kaz': 'Kazakh', 'kk': 'Kazakh',
    'aze': 'Azeri (Latin)', 'az': 'Azeri (Latin)',
    'guj': 'Gujarati', 'gu': 'Gujarati',
    'kan': 'Kannada', 'kn': 'Kannada',
    'mar': 'Marathi', 'mr': 'Marathi',
    'nep': 'Nepali', 'ne': 'Nepali',
    'sin': 'Sinhala', 'si': 'Sinhala',
    'pbu': 'Pashto', 'ps': 'Pashto',
    'lao': 'Lao', 'lo': 'Lao',
    # African languages
    'afr': 'Afrikaans', 'af': 'Afrikaans',
    'swa': 'Swahili', 'sw': 'Swahili',
    'amh': 'Amharic', 'am': 'Amharic',
    'som': 'Somali', 'so': 'Somali',
    'yor': 'Yoruba', 'yo': 'Yoruba',
    'ibo': 'Igbo', 'ig': 'Igbo',
    'xho': 'Xhosa', 'xh': 'Xhosa',
    'zul': 'Zulu', 'zu': 'Zulu',
    'hat': 'Haitian Creole', 'ht': 'Haitian Creole',
    # Other
    'lat': 'Latin', 'la': 'Latin',
    'epo': 'Esperanto', 'eo': 'Esperanto',
    'mol': 'Moldavian', 'mo': 'Moldavian',
    'kir': 'Kyrgyz', 'ky': 'Kyrgyz',
    'tuk': 'Turkmen (Latin)', 'tk': 'Turkmen (Latin)',
    'prs': 'Dari', 'prs-af': 'Dari',
}


def get_language_display_name(code: str) -> str:
    """Get human-readable language name from any memoQ language code.

    Handles 3-letter (eng-US), 2-letter (en-us), and base codes (eng, en).
    Case-insensitive lookup.
    """
    if not code:
        return "Unknown"
    lookup = code.lower().strip()
    name = MEMOQ_LANG_NAMES.get(lookup)
    if name:
        return name
    # Try without locale
    base = lookup.split('-')[0]
    name = MEMOQ_LANG_NAMES.get(base)
    if name:
        # Append locale for clarity
        if '-' in lookup:
            locale = lookup.split('-', 1)[1].upper()
            return f"{name} ({locale})"
        return name
    return code  # Return raw code if nothing matches


def convert_detected_lang(detected_code: str) -> str:
    """Convert auto-detected ISO code to memoQ 3-letter code."""
    if not detected_code:
        return detected_code
    parts = detected_code.split('-')
    if len(parts) == 2:
        base = ISO_TO_MEMOQ_LANG.get(parts[0], parts[0])
        return f"{base}-{parts[1].upper()}"
    return ISO_TO_MEMOQ_LANG.get(detected_code, detected_code)
