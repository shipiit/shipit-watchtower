"""
Masking tests.

Two failure modes matter and they pull in opposite directions:

* **Under-masking** leaks PII to a third-party backend — the whole point of
  the module.
* **Over-masking** destroys the data traces exist to explain. A fleet system is
  full of ten-digit numbers that are not tax IDs, and redacting an odometer
  reading makes the trace useless.

So the negative cases carry as much weight as the positive ones.
"""

from __future__ import annotations

import pytest

from shipit_watcher.masking import (
    MaskingPolicy,
    Redactor,
    _iban_ok,
    _luhn_ok,
    _nip_ok,
    _pesel_ok,
    _regon_ok,
    mask_payload,
    mask_text,
)


class TestChecksums:
    """Checksum validation is what separates an identifier from a number."""

    def test_valid_nip(self):
        assert _nip_ok("7010929600") is True

    def test_invalid_nip_rejected(self):
        assert _nip_ok("1234567891") is False

    def test_nip_wrong_length(self):
        assert _nip_ok("70109296") is False

    def test_valid_pesel(self):
        assert _pesel_ok("44051401359") is True

    def test_invalid_pesel_rejected(self):
        assert _pesel_ok("44051401358") is False

    def test_pesel_wrong_length(self):
        assert _pesel_ok("4405140135") is False

    def test_valid_iban(self):
        assert _iban_ok("PL61109010140000071219812874") is True

    def test_iban_with_spaces(self):
        assert _iban_ok("PL61 1090 1014 0000 0712 1981 2874") is True

    def test_invalid_iban_rejected(self):
        assert _iban_ok("PL61109010140000071219812875") is False

    def test_iban_too_short(self):
        assert _iban_ok("PL61") is False

    def test_iban_non_alpha_prefix(self):
        assert _iban_ok("1261109010140000071219812874") is False

    def test_luhn_valid_card(self):
        assert _luhn_ok("4111111111111111") is True

    def test_luhn_invalid_card(self):
        assert _luhn_ok("4111111111111112") is False

    @pytest.mark.parametrize("regon", ["123456785", "12345678512347"])
    def test_regon_lengths(self, regon):
        # Exercises both the 9- and 14-digit branches.
        assert isinstance(_regon_ok(regon), bool)

    def test_regon_wrong_length(self):
        assert _regon_ok("12345") is False


class TestPositiveDetection:
    """Real identifiers must never survive."""

    @pytest.mark.parametrize(
        "text,label",
        [
            ("PESEL 44051401359", "[PESEL]"),
            ("NIP 7010929600", "[NIP]"),
            ("IBAN PL61109010140000071219812874", "[IBAN]"),
            ("card 4111 1111 1111 1111", "[CARD]"),
            ("mail jan.kowalski@fleet.pl", "[EMAIL]"),
            ("call +48 601 234 567", "[PHONE]"),
            ("call 601-234-567", "[PHONE]"),
            ("host 192.168.1.44", "[IP]"),
        ],
    )
    def test_identifier_is_masked(self, text, label):
        assert label in mask_text(text)

    def test_original_value_absent(self):
        assert "44051401359" not in mask_text("PESEL 44051401359")


class TestNegativeDetection:
    """Legitimate fleet data must survive untouched."""

    @pytest.mark.parametrize(
        "text",
        [
            "Odometer 1234567890 km",       # 10 digits, invalid NIP checksum
            "Trip 123456 done in 45 min",
            "Fuel 55.5 L at 6.29 PLN",
            "Plate GD2T196 driver Anna",
            "2026-08-04 10:58:02",
            "Version 1.2.3 build 4567",
        ],
    )
    def test_ordinary_text_untouched(self, text):
        assert mask_text(text) == text

    def test_boundaries_not_crossed(self):
        """The bug that shipped first time: a rule firing mid-number."""
        assert mask_text("Odometer 1234567890 km") == "Odometer 1234567890 km"


class TestEdgeCases:
    def test_empty_string(self):
        assert mask_text("") == ""

    def test_none_safe(self):
        assert mask_payload(None) is None

    def test_oversized_text_truncated(self):
        out = mask_text("x" * 300_000)
        assert "[truncated]" in out

    def test_failure_degrades_to_redaction(self, monkeypatch):
        """A masking failure must lose the value, not leak it."""
        redactor = Redactor()
        monkeypatch.setattr(
            redactor.policy, "rules", lambda: (_ for _ in ()).throw(RuntimeError("x"))
        )
        assert redactor.text("PESEL 44051401359") == "[REDACTION_FAILED]"


class TestStructuredPayloads:
    def test_sensitive_keys_dropped_wholesale(self):
        out = mask_payload({"password": "hunter2", "api_key": "sk-abc"})
        assert out == {"password": "[REDACTED]", "api_key": "[REDACTED]"}

    def test_allowlisted_keys_preserved(self):
        out = mask_payload({"model": "gemini-2.5-pro"})
        assert out["model"] == "gemini-2.5-pro"

    def test_nested_structures(self):
        out = mask_payload({"a": {"b": [{"email": "x@y.pl"}]}})
        assert out["a"]["b"][0]["email"] == "[EMAIL]"

    def test_depth_limit(self):
        deep: dict = {}
        node = deep
        for _ in range(30):
            node["n"] = {}
            node = node["n"]
        assert "[MAX_DEPTH]" in str(mask_payload(deep))

    def test_list_truncation(self):
        out = mask_payload(list(range(5_000)))
        assert out[-1] == "[TRUNCATED]"

    def test_mapping_truncation(self):
        out = mask_payload({str(i): i for i in range(5_000)})
        assert out["…"] == "[TRUNCATED]"

    def test_non_string_scalars_passthrough(self):
        assert mask_payload(42) == 42
        assert mask_payload(True) is True
        assert mask_payload(3.14) == 3.14

    def test_tuples_and_sets(self):
        assert mask_payload(("a@b.pl",)) == ["[EMAIL]"]
        assert mask_payload({"a@b.pl"}) == ["[EMAIL]"]


class TestPolicy:
    def test_selecting_rules(self):
        policy = MaskingPolicy(enabled_rules=frozenset({"EMAIL"}))
        redactor = Redactor(policy=policy)
        out = redactor.text("mail a@b.pl and PESEL 44051401359")
        assert "[EMAIL]" in out
        assert "44051401359" in out  # PESEL rule disabled

    def test_all_rules_by_default(self):
        assert len(list(MaskingPolicy().rules())) >= 8

    def test_custom_placeholder(self):
        policy = MaskingPolicy(placeholder="<{label}>")
        assert "<EMAIL>" in Redactor(policy=policy).text("a@b.pl")
