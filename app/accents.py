"""Learner-stated interests only; no ability inference or voice selection."""
import re
from app.models import AccentPreferences

LABELS = {'british': 'British English', 'american': 'American English',
    'australian': 'Australian English', 'scottish': 'Scottish English',
    'indian': 'Indian English', 'irish': 'Irish English', 'canadian': 'Canadian English',
    'new_zealand': 'New Zealand English', 'welsh': 'Welsh English', 'south_african': 'South African English'}
ALIASES = {'british': r'british|britain|england|uk',
    'american': r'american|america|usa|us accent', 'australian': r'australian|australia|aussie',
    'scottish': r'scottish|scotland|scots', 'indian': r'indian|india', 'irish': r'irish|ireland',
    'canadian': r'canadian|canada', 'new_zealand': r'new zealand|kiwi',
    'welsh': r'welsh|wales', 'south_african': r'south african|south africa'}
UNAVAILABLE = 'Accent-specific recordings aren’t available yet, so today’s audio will use our usual voice.'

def normalize(text: str) -> list[str]:
    result = []
    for key, aliases in ALIASES.items():
        for match in re.finditer(r'\b(?:' + aliases + r')\b', text, re.I):
            if not re.search(r'\b(?:not|no|except|rather than)\s+$', text[:match.start()], re.I):
                result.append(key)
                break
    return result

def no_preference(text: str) -> bool:
    if re.fullmatch(r'\s*(?:no|nope|not really)[.! ]*', text, re.I):
        return True
    return bool(re.search(r"\b(?:no (?:accent )?preference|not sure|don['’]?t know|none|any accent|skip|no thanks|not today|stop suggesting accents?|forget.*accents?|no more accents?|don['’]?t (?:want|suggest|remember).*accent)\b", text, re.I))

def extract(text: str) -> AccentPreferences | None:
    response = text.strip()
    accents = normalize(response)
    # Tentative wording must not erase explicitly named preferences.
    explicit_decline = re.search(r"\b(?:no (?:accent )?preference|skip|no thanks|not today|stop suggesting|forget|no more accents|don['’]?t (?:want|suggest|remember).*accent)\b", response, re.I)
    if no_preference(response) and (not accents or explicit_decline):
        return AccentPreferences(response=response, status='no_preference')
    if not response or re.fullmatch(r"(?:yes|yeah|maybe|an? accent|something|hmm|okay|please repeat|what do you mean|I don['’]?t understand)[?!. ]*", response, re.I):
        return None
    # Unknown varieties and rate context stay in the exact response, never spoken
    # back as instructions or passed to exercise generation.
    return AccentPreferences(response=response, accents=accents, status='preferred')

def only_accent_request(text: str) -> bool:
    """Conservative grammar for accent-only replies; topic words stay meaningful."""
    remaining = text.lower().replace('’', "'")
    for aliases in ALIASES.values():
        remaining = re.sub(r'\b(?:' + aliases + r')\b', ' ', remaining)
    remaining = re.sub(r"\b[a-z]+(?=\s+(?:accent|english)\b)", ' ', remaining)
    remaining = re.sub(r"\b(?:i'd|i'm|don't|not|sure|know|no|none|thanks|thank|you|skip|preference|"
        r"stop|suggesting|remember|forget|any|an|a|the|and|or|but|please|could|can|we|i|my|me|"
        r"want|would|like|prefer|try|do|hear|practice|listening|listen|to|with|in|today|instead|"
        r"english|accents?|speech|voice|audio|recordings?|fast|faster|slow|slower|speed|rate|tricky|find|difficult)\b", ' ', remaining)
    return not re.search(r'[a-z0-9]', remaining)

def lesson_preference(text: str, suggested: str | None = None) -> AccentPreferences | None:
    if suggested and re.fullmatch(r'\s*(?:no|no thanks|not today|stop suggesting accents?|forget.*accents?|no more accents?|skip|no preference)[.! ]*', text, re.I):
        return AccentPreferences(response=text.strip(), status='no_preference')
    accents = normalize(text)
    named_speech = any(re.search(r'\b(?:' + ALIASES[key] + r')\s+(?:english|speech|voice|audio|recordings?)\b', text, re.I)
                       for key in accents)
    if re.search(r'\baccents?\b', text, re.I) or (accents and (
            only_accent_request(text) or named_speech)):
        return extract(text)
    return None

def acknowledgment(preferences: AccentPreferences) -> str:
    if preferences.status == 'no_preference':
        return 'I won’t suggest an accent preference. We’ll continue with our usual listening practice.'
    labels = ', '.join(LABELS[item] for item in preferences.accents)
    return (f'I’ll remember your interest in {labels}. ' if labels else 'I’ll remember that accent preference. ') + UNAVAILABLE
