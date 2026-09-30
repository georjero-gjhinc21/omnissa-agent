"""Recovery tooling: `reconcile` (read-only comparison of the two
checkpoints) and `requeue` (targeted, backed-up removal of specific ids
so the next ingest re-fetches exactly them). Built for the real incident
where 50 fetched messages were never classified -- these let recovery
be a precise, evidenced, targeted replay instead of a blanket wipe.
"""

import json

from omnissa_agent import cli, state as state_mod


def test_reconcile_reports_fetched_classified_and_pending_counts(tmp_path, capsys):
    ingest_dir = tmp_path / "ingest"
    analysis_dir = tmp_path / "analysis"

    ingest_state = state_mod.load_state(ingest_dir)
    for mid in ("m1", "m2", "m3"):
        state_mod.mark_seen(ingest_state, mid, key="gmail_ingested_ids")
    state_mod.save_state(ingest_state, ingest_dir)

    analysis_state = state_mod.load_state(analysis_dir)
    state_mod.mark_seen(analysis_state, "m1", key="seen_ids")  # only m1 was ever classified
    state_mod.save_state(analysis_state, analysis_dir)

    rc = cli.main(
        [
            "reconcile",
            "--ingest-state-dir", str(ingest_dir),
            "--analysis-state-dir", str(analysis_dir),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "fetched=3 classified=1 pending=2" in out
    assert "m2" in out and "m3" in out
    assert "m1" not in out.split("pending ids")[-1]  # m1 is classified, must not be listed as pending


def test_reconcile_zero_pending_when_fully_caught_up(tmp_path, capsys):
    ingest_dir = tmp_path / "ingest"
    analysis_dir = tmp_path / "analysis"
    ingest_state = state_mod.load_state(ingest_dir)
    state_mod.mark_seen(ingest_state, "m1", key="gmail_ingested_ids")
    state_mod.save_state(ingest_state, ingest_dir)
    analysis_state = state_mod.load_state(analysis_dir)
    state_mod.mark_seen(analysis_state, "m1", key="seen_ids")
    state_mod.save_state(analysis_state, analysis_dir)

    rc = cli.main(
        ["reconcile", "--ingest-state-dir", str(ingest_dir), "--analysis-state-dir", str(analysis_dir)]
    )
    assert rc == 0
    assert "pending=0" in capsys.readouterr().out


def test_requeue_backs_up_before_changing_anything(tmp_path):
    state_dir = tmp_path / "ingest-state"
    st = state_mod.load_state(state_dir)
    for mid in ("m1", "m2", "m3"):
        state_mod.mark_seen(st, mid, key="gmail_ingested_ids")
    state_mod.save_state(st, state_dir)

    backup_dir = tmp_path / "backups"
    rc = cli.main(
        [
            "requeue",
            "--state-dir", str(state_dir),
            "--ids", "m2",
            "--backup-dir", str(backup_dir),
        ]
    )
    assert rc == 0

    backups = list(backup_dir.glob("checkpoint-*.json.bak"))
    assert len(backups) == 1
    backed_up = json.loads(backups[0].read_text())
    assert set(backed_up["gmail_ingested_ids"]) == {"m1", "m2", "m3"}, "backup must be the PRE-change state"
    assert (backups[0].stat().st_mode & 0o777) == 0o600


def test_requeue_removes_only_the_named_ids_targeted_not_blanket(tmp_path):
    state_dir = tmp_path / "ingest-state"
    st = state_mod.load_state(state_dir)
    for mid in ("m1", "m2", "m3", "m4"):
        state_mod.mark_seen(st, mid, key="gmail_ingested_ids")
    state_mod.save_state(st, state_dir)

    rc = cli.main(
        [
            "requeue",
            "--state-dir", str(state_dir),
            "--ids", "m2,m4",
            "--backup-dir", str(tmp_path / "backups"),
        ]
    )
    assert rc == 0

    after = state_mod.load_state(state_dir)
    assert set(after["gmail_ingested_ids"]) == {"m1", "m3"}, "only m2/m4 should be requeued, not a blanket wipe"


def test_requeue_accepts_an_ids_file(tmp_path):
    state_dir = tmp_path / "ingest-state"
    st = state_mod.load_state(state_dir)
    for mid in ("m1", "m2"):
        state_mod.mark_seen(st, mid, key="gmail_ingested_ids")
    state_mod.save_state(st, state_dir)

    ids_file = tmp_path / "pending_ids.txt"
    ids_file.write_text("m1\nm2\n")

    rc = cli.main(
        [
            "requeue",
            "--state-dir", str(state_dir),
            "--ids-file", str(ids_file),
            "--backup-dir", str(tmp_path / "backups"),
        ]
    )
    assert rc == 0
    after = state_mod.load_state(state_dir)
    assert after["gmail_ingested_ids"] == []


def test_requeue_reports_ids_not_present_without_erroring(tmp_path, capsys):
    state_dir = tmp_path / "ingest-state"
    st = state_mod.load_state(state_dir)
    state_mod.mark_seen(st, "m1", key="gmail_ingested_ids")
    state_mod.save_state(st, state_dir)

    rc = cli.main(
        [
            "requeue",
            "--state-dir", str(state_dir),
            "--ids", "m1,m-does-not-exist",
            "--backup-dir", str(tmp_path / "backups"),
        ]
    )
    assert rc == 0
    assert "not present" in capsys.readouterr().err


def test_requeue_refuses_ids_already_classified_when_verify_flag_given(tmp_path, capsys):
    ingest_dir = tmp_path / "ingest-state"
    st = state_mod.load_state(ingest_dir)
    for mid in ("m1", "m2"):
        state_mod.mark_seen(st, mid, key="gmail_ingested_ids")
    state_mod.save_state(st, ingest_dir)

    analysis_dir = tmp_path / "analysis-state"
    analysis_state = state_mod.load_state(analysis_dir)
    state_mod.mark_seen(analysis_state, "m1", key="seen_ids")  # m1 WAS actually classified
    state_mod.save_state(analysis_state, analysis_dir)

    rc = cli.main(
        [
            "requeue",
            "--state-dir", str(ingest_dir),
            "--ids", "m1,m2",  # m1 should NOT be requeued -- it's genuinely done
            "--backup-dir", str(tmp_path / "backups"),
            "--verify-unclassified-against", str(analysis_dir),
        ]
    )
    assert rc == cli.REFUSED
    assert "m1" in capsys.readouterr().err
    # nothing changed -- the whole call refuses rather than partially applying
    assert state_mod.load_state(ingest_dir)["gmail_ingested_ids"] == ["m1", "m2"]


def test_requeue_verify_flag_allows_genuinely_unclassified_ids(tmp_path):
    ingest_dir = tmp_path / "ingest-state"
    st = state_mod.load_state(ingest_dir)
    for mid in ("m1", "m2"):
        state_mod.mark_seen(st, mid, key="gmail_ingested_ids")
    state_mod.save_state(st, ingest_dir)

    analysis_dir = tmp_path / "analysis-state"
    state_mod.save_state(state_mod.load_state(analysis_dir), analysis_dir)  # nothing classified yet

    rc = cli.main(
        [
            "requeue",
            "--state-dir", str(ingest_dir),
            "--ids", "m1,m2",
            "--backup-dir", str(tmp_path / "backups"),
            "--verify-unclassified-against", str(analysis_dir),
        ]
    )
    assert rc == 0
    assert state_mod.load_state(ingest_dir)["gmail_ingested_ids"] == []


def test_reconcile_and_requeue_full_incident_recovery_flow(tmp_path, capsys):
    """Replay the real incident's shape end to end: 50 (here 3) messages
    fetched, none classified (drop overwritten before consumption) ->
    reconcile finds them -> requeue removes them from ingest's checkpoint
    -> reconcile confirms pending is gone from THAT side (a real replay
    would then re-fetch via `ingest`, covered by other tests)."""
    ingest_dir = tmp_path / "ingest"
    analysis_dir = tmp_path / "analysis"
    ingest_state = state_mod.load_state(ingest_dir)
    for mid in ("m1", "m2", "m3"):
        state_mod.mark_seen(ingest_state, mid, key="gmail_ingested_ids")
    state_mod.save_state(ingest_state, ingest_dir)
    # analysis never classified anything -- exactly the incident's shape
    state_mod.save_state(state_mod.load_state(analysis_dir), analysis_dir)

    cli.main(["reconcile", "--ingest-state-dir", str(ingest_dir), "--analysis-state-dir", str(analysis_dir)])
    assert "pending=3" in capsys.readouterr().out

    rc = cli.main(
        [
            "requeue",
            "--state-dir", str(ingest_dir),
            "--ids", "m1,m2,m3",
            "--backup-dir", str(tmp_path / "backups"),
        ]
    )
    assert rc == 0
    assert state_mod.load_state(ingest_dir)["gmail_ingested_ids"] == []
