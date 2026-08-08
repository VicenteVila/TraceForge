"""PII detection and masking for captured trace data.

Regex-based by default (fast, dependency-free). Optional lightweight NER via
``spacy`` if installed and enabled. Masking is applied to captured inputs and
outputs so sensitive data never lands in traces.
"""

import re
from typing import Any, Optional

DEFAULT_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "phone": re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{3,4}"),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "ipv4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}

_NER_LABELS = {"PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "CREDIT_CARD", "IP_ADDRESS"}


class PIIMasker:
    def __init__(
        self,
        enabled: bool = True,
        patterns: Optional[dict[str, re.Pattern]] = None,
        use_ner: bool = False,
    ):
        self.enabled = enabled
        self.patterns = dict(patterns or DEFAULT_PATTERNS)
        self.use_ner = use_ner
        self._nlp = None

    def redact(self, text: str) -> tuple[str, int]:
        """Return (masked_text, hits) replacing every PII match with a label."""
        if not text:
            return text, 0
        hits = 0
        for label, pattern in self.patterns.items():
            if pattern is None:
                continue
            masked, count = pattern.subn(f"<{label}>", text)
            if count:
                text = masked
                hits += count
        if self.use_ner:
            nlp = self._get_nlp()
            if nlp is not None:
                doc = nlp(text)
                for ent in doc.ents:
                    if ent.label_ in _NER_LABELS:
                        text = text[: ent.start_char] + f"<{ent.label_}>" + text[ent.end_char :]
                        hits += 1
        return text, hits

    def find(self, text: str) -> list[tuple[str, str]]:
        """Return [(label, matched_value)] for every PII found."""
        found: list[tuple[str, str]] = []
        for label, pattern in self.patterns.items():
            if pattern is None:
                continue
            found.extend((label, m.group(0)) for m in pattern.finditer(text))
        return found

    def _get_nlp(self):
        if self._nlp is not None:
            return self._nlp
        try:
            import spacy  # type: ignore

            try:
                self._nlp = spacy.load("en_core_web_sm")
            except OSError:
                self._nlp = None
        except ImportError:
            self._nlp = None
        return self._nlp


_masker = PIIMasker()


def set_pii_masker(
    enabled: Optional[bool] = None,
    patterns: Optional[dict[str, Any]] = None,
    use_ner: Optional[bool] = None,
) -> None:
    """Configure global PII masking. Patterns accept compiled or raw regexes."""
    global _masker
    if enabled is not None:
        _masker.enabled = enabled
    if use_ner is not None:
        _masker.use_ner = use_ner
    if patterns is not None:
        compiled = {label: re.compile(p) if isinstance(p, str) else p for label, p in patterns.items()}
        _masker.patterns.update(compiled)


def redact_value(value: Any) -> tuple[Any, int]:
    """Recursively mask PII in a captured value. Returns (masked, hits)."""
    if not _masker.enabled:
        return value, 0
    return _redact(value)


def _redact(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        masked, hits = _masker.redact(value)
        return masked, hits
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        hits = 0
        for k, v in value.items():
            mk, h = _redact(v)
            result[k] = mk
            hits += h
        return result, hits
    if isinstance(value, (list, tuple)):
        items = [_redact(v) for v in value]
        if isinstance(value, tuple):
            return tuple(v for v, _ in items), sum(h for _, h in items)
        return [v for v, _ in items], sum(h for _, h in items)
    return value, 0
