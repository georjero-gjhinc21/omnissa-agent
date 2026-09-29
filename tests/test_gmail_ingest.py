from omnissa_agent import gmail_ingest as gi
from omnissa_agent.gmail_api import GmailApiError, GmailAuthError, GmailRateLimitError

GOOD_LABEL = {"id": "Label_42", "name": gi.EXPECTED_LABEL_NAME}


class FakeClient:
    """Duck-typed stand-in for GmailReadonlyClient -- no network, no tokens."""

    def __init__(self, *, profile_email="george@gjh-inc.com", labels=None, pages=None, messages=None,
                 profile_error=None, list_error=None):
        self._profile_email = profile_email
        self._labels = labels if labels is not None else [GOOD_LABEL]
        self._pages = pages if pages is not None else [([], None)]
        self._messages = messages or {}
        self._profile_error = profile_error
        self._list_error = list_error
        self._page_calls = 0
        self.get_message_calls = []

    def get_profile(self):
        if self._profile_error:
            raise self._profile_error
        return {"emailAddress": self._profile_email}

    def list_labels(self):
        return self._labels

    def list_message_ids(self, *, label_id, page_token=None, max_results=25):
        if self._list_error:
            raise self._list_error
        ids, next_token = self._pages[self._page_calls]
        self._page_calls += 1
        return ids, next_token

    def get_message_metadata(self, message_id):
        self.get_message_calls.append(message_id)
        entry = self._messages[message_id]
        if isinstance(entry, Exception):
            raise entry
        return entry


def _raw_message(mid, *, label_ids=("Label_42",), subject="Subj", sender="a@omnissa.com", date="2026-09-29", snippet="hi"):
    return {
        "id": mid,
        "snippet": snippet,
        "labelIds": list(label_ids),
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "Date", "value": date},
            ]
        },
    }


def test_wrong_account_refuses_before_any_label_or_message_call():
    client = FakeClient(profile_email="someone-else@gjh-inc.com")
    result = gi.run_ingestion(client)
    assert result.status == gi.IngestStatus.WRONG_ACCOUNT
    assert result.messages == []


def test_auth_failure_on_profile():
    client = FakeClient(profile_error=GmailAuthError(401, "invalid_token"))
    result = gi.run_ingestion(client)
    assert result.status == gi.IngestStatus.AUTH_FAILURE


def test_missing_label_is_distinguished_from_wrong_account():
    client = FakeClient(labels=[{"id": "L1", "name": "INBOX"}])
    result = gi.run_ingestion(client)
    assert result.status == gi.IngestStatus.LABEL_MISSING


def test_similarly_named_label_does_not_count_as_a_match():
    # every previously-guessed name (repo docs' "Omnissa", the bare
    # "@omnissa.com" without its real "Archive_/" parent, etc.) must NOT
    # satisfy the exact match -- only the confirmed real name counts
    client = FakeClient(
        labels=[
            {"id": "L1", "name": "Omnissa"},
            {"id": "L2", "name": "omnissa.com"},
            {"id": "L3", "name": "@omnissa.com"},  # missing the real "Archive_/" prefix
        ]
    )
    result = gi.run_ingestion(client)
    assert result.status == gi.IngestStatus.LABEL_MISSING


def test_ambiguous_label_when_two_exact_matches_exist():
    client = FakeClient(labels=[GOOD_LABEL, {"id": "Label_99", "name": gi.EXPECTED_LABEL_NAME}])
    result = gi.run_ingestion(client)
    assert result.status == gi.IngestStatus.LABEL_AMBIGUOUS


def test_rate_limited_during_listing(tmp_path):
    client = FakeClient(list_error=GmailRateLimitError(429, "quota"))
    result = gi.run_ingestion(client, state_base=tmp_path)
    assert result.status == gi.IngestStatus.RATE_LIMITED


def test_pagination_across_multiple_pages_bounded_by_max_messages(tmp_path):
    client = FakeClient(
        pages=[(["m1", "m2"], "tok1"), (["m3", "m4"], "tok2"), (["m5"], None)],
        messages={f"m{i}": _raw_message(f"m{i}") for i in range(1, 6)},
    )
    result = gi.run_ingestion(client, state_base=tmp_path, max_pages=5, max_messages=3)
    assert result.status == gi.IngestStatus.OK
    assert len(result.messages) == 3
    assert [m.id for m in result.messages] == ["m1", "m2", "m3"]


def test_label_removed_between_list_and_get_is_rejected_not_included(tmp_path):
    client = FakeClient(
        pages=[(["m1", "m2"], None)],
        messages={
            "m1": _raw_message("m1", label_ids=("Label_42",)),
            "m2": _raw_message("m2", label_ids=("SomeOtherLabel",)),  # label gone by get-time
        },
    )
    result = gi.run_ingestion(client, state_base=tmp_path)
    assert result.status == gi.IngestStatus.OK
    assert [m.id for m in result.messages] == ["m1"]
    assert result.rejected_stale_label_ids == ["m2"]


def test_malformed_message_is_skipped_not_fatal(tmp_path):
    client = FakeClient(
        pages=[(["m1", "m2"], None)],
        messages={
            "m1": _raw_message("m1"),
            "m2": GmailApiError(500, "server error"),
        },
    )
    result = gi.run_ingestion(client, state_base=tmp_path)
    assert result.status == gi.IngestStatus.OK
    assert [m.id for m in result.messages] == ["m1"]
    assert result.malformed_ids == ["m2"]


def test_duplicates_across_runs_are_deduped_via_checkpoint(tmp_path):
    client1 = FakeClient(pages=[(["m1", "m2"], None)], messages={
        "m1": _raw_message("m1"), "m2": _raw_message("m2"),
    })
    first = gi.run_ingestion(client1, state_base=tmp_path)
    assert len(first.messages) == 2

    client2 = FakeClient(pages=[(["m1", "m2", "m3"], None)], messages={
        "m1": _raw_message("m1"), "m2": _raw_message("m2"), "m3": _raw_message("m3"),
    })
    second = gi.run_ingestion(client2, state_base=tmp_path)
    assert [m.id for m in second.messages] == ["m3"]
    assert second.duplicates_skipped == 2


def test_older_message_newly_labeled_is_still_picked_up(tmp_path):
    # first run sees m1 (recent), m3 (old) is not yet labeled so absent from listing
    client1 = FakeClient(
        pages=[(["m1"], None)],
        messages={"m1": _raw_message("m1", date="2026-09-29")},
    )
    gi.run_ingestion(client1, state_base=tmp_path)

    # m3 gets labeled later even though it's an old message -- full label
    # listing includes it on the next run, and it was never in the
    # checkpoint, so dedup does not skip it
    client2 = FakeClient(
        pages=[(["m1", "m3"], None)],
        messages={
            "m1": _raw_message("m1", date="2026-09-29"),
            "m3": _raw_message("m3", date="2020-01-01"),
        },
    )
    second = gi.run_ingestion(client2, state_base=tmp_path)
    assert [m.id for m in second.messages] == ["m3"]


def test_total_deadline_stops_pagination_early_with_partial_ok_result(tmp_path, monkeypatch):
    # 3 pages available, but the deadline elapses after the first page
    client = FakeClient(
        pages=[(["m1"], "tok1"), (["m2"], "tok2"), (["m3"], None)],
        messages={"m1": _raw_message("m1"), "m2": _raw_message("m2"), "m3": _raw_message("m3")},
    )
    clock = {"t": 0.0}

    def fake_monotonic():
        clock["t"] += 1.0  # first call establishes deadline_at; later calls advance past it fast
        return clock["t"] * 1000  # jump far past any real deadline after the first page

    monkeypatch.setattr(gi.time, "monotonic", fake_monotonic)
    result = gi.run_ingestion(client, state_base=tmp_path, max_pages=5, deadline_s=1.0)
    assert result.status == gi.IngestStatus.OK
    assert result.deadline_hit is True
    assert len(result.messages) <= 1  # stopped well before all 3 pages/messages


def test_deadline_not_hit_reports_false_on_a_fast_normal_run(tmp_path):
    client = FakeClient(pages=[(["m1"], None)], messages={"m1": _raw_message("m1")})
    result = gi.run_ingestion(client, state_base=tmp_path, deadline_s=90.0)
    assert result.status == gi.IngestStatus.OK
    assert result.deadline_hit is False


def test_zero_matching_messages_is_ok_status_with_empty_list(tmp_path):
    client = FakeClient(pages=[([], None)])
    result = gi.run_ingestion(client, state_base=tmp_path)
    assert result.status == gi.IngestStatus.OK
    assert result.messages == []


def test_filtered_message_never_carries_raw_gmail_payload_shape():
    raw = _raw_message("m1")
    msg = gi._to_gmail_message(raw)
    # only the restricted fields exist -- no 'payload', no headers blob
    assert set(vars(msg).keys()) == {"id", "subject", "snippet", "sender", "date", "label_ids"}
