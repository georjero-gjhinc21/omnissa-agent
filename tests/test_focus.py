"""Stage 1 -- validated, labeled focus instructions (focus.py).

Covers: valid/invalid instruction formats, unknown categories, missing/
ambiguous label, staleness, and -- critically -- that content anywhere
OTHER than the Subject header of a message under the exact
Agent-Focus label can never be mistaken for an instruction (the
prompt-injection boundary this module exists to enforce).
"""

from datetime import datetime, timedelta, timezone

from omnissa_agent import focus
from omnissa_agent.gmail_api import GmailApiError


class FakeClient:
    """Duck-typed stand-in for GmailReadonlyClient -- no network, no tokens."""

    def __init__(self, *, labels=None, ids=None, messages=None, list_labels_error=None):
        self._labels = labels if labels is not None else []
        self._ids = ids if ids is not None else []
        self._messages = messages or {}
        self._list_labels_error = list_labels_error

    def list_labels(self):
        if self._list_labels_error:
            raise self._list_labels_error
        return self._labels

    def list_message_ids(self, *, label_id, page_token=None, max_results=25):
        return self._ids, None

    def get_message_metadata(self, message_id):
        entry = self._messages[message_id]
        if isinstance(entry, Exception):
            raise entry
        return entry


FOCUS_LABEL = {"id": "Label_9", "name": focus.FOCUS_LABEL_NAME}


def _msg(subject, date):
    return {
        "id": "f1",
        "payload": {"headers": [{"name": "Subject", "value": subject}, {"name": "Date", "value": date}]},
    }


FRESH_DATE = "Wed, 30 Sep 2026 12:00:00 +0000"
NOW = datetime(2026, 9, 30, 13, 0, 0, tzinfo=timezone.utc)  # 1 hour after FRESH_DATE


def test_valid_fresh_instruction_resolves_to_its_category():
    client = FakeClient(labels=[FOCUS_LABEL], ids=["f1"], messages={"f1": _msg("Focus: Renewal", FRESH_DATE)})
    result = focus.resolve_focus_instruction(client, now=NOW)
    assert result == focus.FocusInstruction(category="Renewal", source_message_id="f1")


def test_category_matching_is_case_insensitive():
    client = FakeClient(labels=[FOCUS_LABEL], ids=["f1"], messages={"f1": _msg("focus: renewal", FRESH_DATE)})
    result = focus.resolve_focus_instruction(client, now=NOW)
    assert result.category == "Renewal"


def test_missing_focus_label_means_no_instruction():
    client = FakeClient(labels=[], ids=[], messages={})
    assert focus.resolve_focus_instruction(client, now=NOW) is None


def test_ambiguous_focus_label_means_no_instruction():
    client = FakeClient(labels=[FOCUS_LABEL, FOCUS_LABEL], ids=["f1"], messages={"f1": _msg("Focus: Renewal", FRESH_DATE)})
    assert focus.resolve_focus_instruction(client, now=NOW) is None


def test_no_messages_under_the_label_means_no_instruction():
    client = FakeClient(labels=[FOCUS_LABEL], ids=[], messages={})
    assert focus.resolve_focus_instruction(client, now=NOW) is None


def test_unrecognized_category_is_invalid_not_a_guess():
    client = FakeClient(labels=[FOCUS_LABEL], ids=["f1"], messages={"f1": _msg("Focus: Marketing Budget", FRESH_DATE)})
    assert focus.resolve_focus_instruction(client, now=NOW) is None


def test_missing_focus_prefix_is_not_an_instruction():
    """Content that merely NAMES a category, without the exact 'Focus:'
    format, must never be treated as an instruction -- this is the
    boundary against a message's other content (subject text that
    happens to mention a category, a snippet, a body) being confused
    with a deliberate directive."""
    client = FakeClient(labels=[FOCUS_LABEL], ids=["f1"], messages={"f1": _msg("Renewal reminder for Q4", FRESH_DATE)})
    assert focus.resolve_focus_instruction(client, now=NOW) is None


def test_stale_instruction_is_ignored():
    old_date = "Mon, 01 Jan 2024 12:00:00 +0000"
    client = FakeClient(labels=[FOCUS_LABEL], ids=["f1"], messages={"f1": _msg("Focus: Renewal", old_date)})
    assert focus.resolve_focus_instruction(client, now=NOW) is None


def test_unparseable_date_is_treated_as_stale_never_as_fresh():
    client = FakeClient(labels=[FOCUS_LABEL], ids=["f1"], messages={"f1": _msg("Focus: Renewal", "not a date")})
    assert focus.resolve_focus_instruction(client, now=NOW) is None


def test_api_error_resolving_labels_means_no_instruction_not_a_crash():
    client = FakeClient(list_labels_error=GmailApiError(500, "boom"))
    assert focus.resolve_focus_instruction(client, now=NOW) is None


def test_a_message_under_the_regular_omnissa_label_is_never_mistaken_for_an_instruction():
    """The prompt-injection boundary: even a perfectly-formatted 'Focus:'
    subject line carries no weight unless the message is ALSO under the
    exact Agent-Focus label. A message under Archive_/@omnissa.com (or
    any other label) that happens to say 'Focus: Renewal' must not
    resolve to an instruction -- list_labels here doesn't even have
    Agent-Focus, reproducing exactly that scenario."""
    client = FakeClient(labels=[{"id": "Label_1", "name": "Archive_/@omnissa.com"}], ids=["f1"],
                         messages={"f1": _msg("Focus: Renewal", FRESH_DATE)})
    assert focus.resolve_focus_instruction(client, now=NOW) is None


def test_from_json_dict_rejects_an_unrecognized_category():
    """A corrupt or foreign focus file must never be trusted blindly."""
    assert focus.FocusInstruction.from_json_dict({"category": "Not A Real Category", "source_message_id": "x"}) is None


def test_from_json_dict_rejects_a_missing_source_id():
    assert focus.FocusInstruction.from_json_dict({"category": "Renewal"}) is None


def test_from_json_dict_accepts_a_valid_record():
    result = focus.FocusInstruction.from_json_dict({"category": "Renewal", "source_message_id": "f1"})
    assert result == focus.FocusInstruction(category="Renewal", source_message_id="f1")
