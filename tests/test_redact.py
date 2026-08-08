import json
import re
import sys

from traceforge import set_pii_masker, trace
from traceforge.collector.memory import MemoryCollector
from traceforge.redact import DEFAULT_PATTERNS, PIIMasker, redact_value


def test_redact_value_masks_common_pii():
    value, hits = redact_value(
        {
            "email": "maria.garcia@acme.com",
            "phone": "+34 600 123 456",
            "card": "4111 1111 1111 1111",
            "ssn": "123-45-6789",
            "ip": "192.168.1.1",
        }
    )
    assert hits == 5
    assert value["email"] == "<email>"
    assert value["phone"] == "<phone>"
    assert value["card"] == "<credit_card>"
    assert value["ssn"] == "<ssn>"
    assert value["ip"] == "<ipv4>"


def test_redact_value_is_recursive():
    value, hits = redact_value(
        {
            "nested": {"deep": ["msg to a@b.com"]},
            "tup": ("hi", "call 555-010-1234"),
        }
    )
    assert hits == 2
    assert value["nested"]["deep"] == ["msg to <email>"]
    assert value["tup"] == ("hi", "call <phone>")


def test_redact_value_leaves_clean_text_untouched():
    value, hits = redact_value("just a normal response, no pii here")
    assert value == "just a normal response, no pii here"
    assert hits == 0


def test_masker_can_be_disabled():
    set_pii_masker(enabled=False)
    try:
        value, hits = redact_value("write to a@b.com")
        assert value == "write to a@b.com"
        assert hits == 0
    finally:
        set_pii_masker(enabled=True)


def test_custom_patterns():
    masker = PIIMasker(patterns={**DEFAULT_PATTERNS, "secret": re.compile(r"TOKEN-\d+")})
    text, hits = masker.redact("key TOKEN-42 done")
    assert text == "key <secret> done"
    assert hits == 1


def test_find_returns_matches():
    masker = PIIMasker()
    found = masker.find("contact bob@example.com or 911")
    labels = {label for label, _ in found}
    assert "email" in labels


def test_trace_redacts_captured_input_and_output():
    collector = MemoryCollector()

    @trace(agent="pii_agent", collector=collector)
    def echo(msg: str) -> str:
        return f"thanks {msg}"

    echo("from bob@example.com, call +34 600 123 456")

    spans = collector.get_trace(collector.get_last_trace_id())
    serialized_input = json.dumps(spans[0].input)
    assert "bob@example.com" not in serialized_input
    assert "600 123 456" not in serialized_input
    assert "<email>" in serialized_input
    assert "<phone>" in serialized_input
    assert "<email>" in spans[0].output
    assert "bob@example.com" not in spans[0].output


def test_masker_skips_none_patterns():
    masker = PIIMasker(patterns={"disabled": None, "email": DEFAULT_PATTERNS["email"]})
    text, hits = masker.redact("hi bob@example.com")
    assert text == "hi <email>"
    assert hits == 1

    found = masker.find("hi bob@example.com")
    assert found == [("email", "bob@example.com")]


def test_set_pii_masker_accepts_raw_regex_strings():
    set_pii_masker(patterns={"internal_id": r"ID-\d{5}"})
    try:
        value, hits = redact_value("ref ID-12345 ok")
        assert value == "ref <internal_id> ok"
        assert hits == 1
    finally:
        set_pii_masker(patterns={"internal_id": None})
        set_pii_masker(enabled=True)


class _FakeEnt:
    def __init__(self, label, start, end):
        self.label_ = label
        self.start_char = start
        self.end_char = end


class _FakeDoc:
    def __init__(self, ents):
        self.ents = ents


class _FakeNLP:
    def __call__(self, text):
        first = text.find("Ada Lovelace")
        if first >= 0:
            return _FakeDoc([_FakeEnt("PERSON", first, first + len("Ada Lovelace"))])
        return _FakeDoc([])


def test_ner_masks_person_when_spacy_available(monkeypatch):
    class _FakeSpacy:
        @staticmethod
        def load(_name):
            return _FakeNLP()

    monkeypatch.setitem(sys.modules, "spacy", _FakeSpacy)
    masker = PIIMasker(use_ner=True)
    text, hits = masker.redact("Signed by Ada Lovelace")
    assert text == "Signed by <PERSON>"
    assert hits == 1
    assert masker._nlp is not None


def test_ner_silently_disables_when_model_missing(monkeypatch):
    class _NoModel:
        @staticmethod
        def load(_name):
            raise OSError("model not found")

    monkeypatch.setitem(sys.modules, "spacy", _NoModel)
    masker = PIIMasker(use_ner=True)
    text, hits = masker.redact("Signed by Ada Lovelace")
    assert text == "Signed by Ada Lovelace"
    assert hits == 0
    assert masker._nlp is None


def test_ner_silently_disables_when_spacy_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "spacy", None)
    masker = PIIMasker(use_ner=True)
    text, hits = masker.redact("Signed by Ada Lovelace")
    assert text == "Signed by Ada Lovelace"
    assert hits == 0
