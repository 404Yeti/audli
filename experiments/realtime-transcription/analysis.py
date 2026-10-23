"""Pure scoring and summary functions for the experiment (no network, no files)."""
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.evaluate_stt import normalize, wer as _wer  # noqa: E402  (reuse the production harness' word error rate)

FILLERS = {'um', 'uh', 'er', 'erm', 'ah', 'hmm', 'mm', 'umm', 'uhh'}
FALSE_RELIABLE_WER = 0.25
FALSE_UNRELIABLE_WER = 0.10


def strip_fillers(text: str) -> str:
    return ' '.join(word for word in normalize(text) if word not in FILLERS)


def score(reference: str, hypothesis: str, must_contain: tuple[str, ...] = ()) -> dict:
    """WER ignoring hesitation fillers on both sides, plus whether the required (self-corrected) words survived."""
    ref, hyp = strip_fillers(reference), strip_fillers(hypothesis)
    words = set(normalize(hypothesis))
    return {'wer': round(_wer(ref, hyp), 4) if ref else None,
            'kept_required': all(token in words for token in must_contain) if must_contain else None}


def percentile(values, q):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 3) if values else None


def summarize_numbers(values) -> dict:
    values = [v for v in values if v is not None]
    return {'n': len(values), 'p50': percentile(values, .5), 'p90': percentile(values, .9),
            'mean': round(statistics.fmean(values), 3) if values else None, 'max': round(max(values), 3) if values else None}


def endpoint_outcome(vad: dict, true_speech_end_ms: int) -> str:
    """cut_off: the detector ended the turn while the learner was still going to speak. late: waited past the end."""
    if not vad['ended']:
        return 'never_ended'
    return 'cut_off' if vad['end_ms'] < true_speech_end_ms else 'ok'


def gate_metrics(rows: list[dict], accepted_key: str, wer_key: str) -> dict:
    """False-reliable: accepted although WER > 0.25. False-unreliable: rejected although WER <= 0.10."""
    scored = [r for r in rows if r.get(wer_key) is not None]
    accepted = [r for r in scored if r[accepted_key]]
    rejected = [r for r in scored if not r[accepted_key]]
    return {'n': len(scored), 'accepted': len(accepted),
            'pass_rate': round(len(accepted) / len(scored), 4) if scored else None,
            'false_reliable': sum(r[wer_key] > FALSE_RELIABLE_WER for r in accepted),
            'false_reliable_rate': round(sum(r[wer_key] > FALSE_RELIABLE_WER for r in accepted) / len(accepted), 4) if accepted else None,
            'false_unreliable': sum(r[wer_key] <= FALSE_UNRELIABLE_WER for r in rejected),
            'false_unreliable_rate': round(sum(r[wer_key] <= FALSE_UNRELIABLE_WER for r in rejected) / len(rejected), 4) if rejected else None}


def agreement_gate(stream_text: str, rest_text: str, threshold: float, minimum_words: int = 3) -> bool:
    """Safeguard S1: trust the streaming text only if an independent transcription of the same audio agrees."""
    if len(normalize(stream_text)) < minimum_words or not normalize(rest_text):
        return False
    return _wer(strip_fillers(rest_text), strip_fillers(stream_text)) <= threshold if strip_fillers(rest_text) else False


def languages_gate(languages: list[dict] | None) -> bool:
    """Safeguard S2: gpt-transcribe returns an empty `languages` array when it cannot predict reliably."""
    return bool(languages) and any(item.get('code', '').startswith('en') for item in languages)


def estimated_lesson_cost(price_per_minute: float, mic_open_minutes: float) -> float:
    return round(price_per_minute * mic_open_minutes, 4)
