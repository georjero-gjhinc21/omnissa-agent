from omnissa_agent import cli


def _base_args(tmp_path, confidence):
    return [
        "draft",
        "--message-id",
        "m1",
        "--category",
        "Training",
        "--summary",
        "Human-typed summary of a real situation, not extracted from the message.",
        "--source-ref",
        "gmail:m1",
        "--confidence",
        confidence,
        "--note",
        "Checked techzone.omnissa.com program page on 2026-09-29; matches this offer.",
        "--state-dir",
        str(tmp_path),
    ]


def test_confirmed_confidence_writes_a_reviewable_draft(tmp_path, capsys):
    rc = cli.main(_base_args(tmp_path, "Confirmed"))
    assert rc == 0
    files = list((tmp_path / "reports").glob("*-draft-m1.md"))
    assert len(files) == 1
    text = files[0].read_text()
    assert "DRAFT ONLY -- NOT SENT" in text
    assert "Human-provided evidence note" in text
    assert (files[0].stat().st_mode & 0o777) == 0o600
    out = capsys.readouterr().out
    assert "wrote " in out


def test_likely_confidence_also_drafts(tmp_path):
    rc = cli.main(_base_args(tmp_path, "Likely"))
    assert rc == 0
    assert list((tmp_path / "reports").glob("*-draft-m1.md"))


def test_unverified_confidence_refuses_to_draft(tmp_path, capsys):
    rc = cli.main(_base_args(tmp_path, "Unverified"))
    assert rc == cli.REFUSED
    assert not (tmp_path / "reports").exists()
    assert "does not meet" in capsys.readouterr().err


def test_draft_never_claims_eligibility_or_benefit(tmp_path):
    cli.main(_base_args(tmp_path, "Confirmed"))
    text = list((tmp_path / "reports").glob("*-draft-m1.md"))[0].read_text()
    lowered = text.lower()
    for claim in ("you are eligible", "you qualify", "confirmed eligible", "guaranteed"):
        assert claim not in lowered
    assert "could you confirm" in lowered  # it asks, it does not assert
