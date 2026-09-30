import importlib

from omnissa_agent import state as state_mod
from omnissa_agent.lock import AlreadyRunningError, SingleInstanceLock


def test_omnissa_agent_state_dir_env_var_still_wins_first(monkeypatch):
    """The live deployed units don't set this env var at all (they pass
    --state-dir explicitly), but anything else relying on it must keep
    working unchanged through the partner_agent rebrand -- checked
    FIRST, ahead of the new PARTNER_AGENT_STATE_DIR name."""
    monkeypatch.setenv("OMNISSA_AGENT_STATE_DIR", "/tmp/old-name-wins")
    monkeypatch.setenv("PARTNER_AGENT_STATE_DIR", "/tmp/new-name-loses")
    importlib.reload(state_mod)
    try:
        assert str(state_mod.DEFAULT_STATE_DIR) == "/tmp/old-name-wins"
    finally:
        monkeypatch.delenv("OMNISSA_AGENT_STATE_DIR", raising=False)
        monkeypatch.delenv("PARTNER_AGENT_STATE_DIR", raising=False)
        importlib.reload(state_mod)  # restore the real default for subsequent tests


def test_partner_agent_state_dir_env_var_works_when_old_one_is_unset(monkeypatch):
    monkeypatch.delenv("OMNISSA_AGENT_STATE_DIR", raising=False)
    monkeypatch.setenv("PARTNER_AGENT_STATE_DIR", "/tmp/new-name-used")
    importlib.reload(state_mod)
    try:
        assert str(state_mod.DEFAULT_STATE_DIR) == "/tmp/new-name-used"
    finally:
        monkeypatch.delenv("PARTNER_AGENT_STATE_DIR", raising=False)
        importlib.reload(state_mod)


def test_save_state_preserves_original_owner_when_run_as_root(tmp_path, monkeypatch):
    """Regression for a real, live production outage (2026-09-30): a
    root-run save (e.g. `requeue --verify-unclassified-against`, which
    must run as root to read a second identity's private checkpoint)
    silently rewrote the checkpoint as root-owned via the atomic
    tmp+replace, and the systemd service running as the file's ORIGINAL
    owning identity could then no longer even read its own checkpoint.
    """
    st = state_mod.load_state(tmp_path)
    state_mod.mark_seen(st, "m1")
    state_mod.save_state(st, tmp_path)
    checkpoint = tmp_path / "checkpoint.json"
    original_uid_gid = (checkpoint.stat().st_uid, checkpoint.stat().st_gid)

    chown_calls = []
    monkeypatch.setattr(state_mod.os, "geteuid", lambda: 0)  # simulate running as root
    monkeypatch.setattr(state_mod.os, "chown", lambda path, uid, gid: chown_calls.append((uid, gid)))

    st2 = state_mod.load_state(tmp_path)
    state_mod.mark_seen(st2, "m2")
    state_mod.save_state(st2, tmp_path)

    assert chown_calls == [original_uid_gid], (
        "a root-run save must chown the rewritten file back to its original owner"
    )


def test_save_state_does_not_chown_when_not_running_as_root(tmp_path, monkeypatch):
    state_mod.save_state(state_mod.load_state(tmp_path), tmp_path)  # create the file first

    chown_calls = []
    monkeypatch.setattr(state_mod.os, "chown", lambda *a: chown_calls.append(a))
    # os.geteuid is NOT mocked here -- this test runs as the normal
    # (non-root) test user, so no chown should ever be attempted
    state_mod.save_state(state_mod.load_state(tmp_path), tmp_path)
    assert chown_calls == []


def test_save_state_first_ever_write_as_root_does_not_attempt_chown(tmp_path, monkeypatch):
    """No pre-existing file means no original owner to preserve."""
    monkeypatch.setattr(state_mod.os, "geteuid", lambda: 0)
    chown_calls = []
    monkeypatch.setattr(state_mod.os, "chown", lambda *a: chown_calls.append(a))
    state_mod.save_state(state_mod.load_state(tmp_path), tmp_path)
    assert chown_calls == []


def test_save_state_chown_failure_does_not_crash_the_save(tmp_path, monkeypatch):
    """Best-effort: if chown itself fails for some other reason, the save
    must still complete (the file remains at least root-readable) rather
    than raising and losing the write entirely."""
    state_mod.save_state(state_mod.load_state(tmp_path), tmp_path)
    monkeypatch.setattr(state_mod.os, "geteuid", lambda: 0)

    def boom(*a):
        raise OSError("simulated chown failure")

    monkeypatch.setattr(state_mod.os, "chown", boom)
    st = state_mod.load_state(tmp_path)
    state_mod.mark_seen(st, "m1")
    state_mod.save_state(st, tmp_path)  # must not raise
    assert "m1" in state_mod.load_state(tmp_path)["seen_ids"]


def test_state_roundtrip_and_permissions(tmp_path):
    st = state_mod.load_state(tmp_path)
    assert st == {"seen_ids": [], "gmail_ingested_ids": [], "last_run": None}

    state_mod.mark_seen(st, "msg-1")
    state_mod.save_state(st, tmp_path)

    reloaded = state_mod.load_state(tmp_path)
    assert state_mod.is_seen(reloaded, "msg-1")
    assert not state_mod.is_seen(reloaded, "msg-2")

    path = tmp_path / "checkpoint.json"
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_dedup_new_preserves_order(tmp_path):
    st = state_mod.load_state(tmp_path)
    state_mod.mark_seen(st, "a")
    assert state_mod.dedup_new(st, ["a", "b", "c", "b"]) == ["b", "c", "b"]


def test_restart_recovery_does_not_reprocess(tmp_path):
    st = state_mod.load_state(tmp_path)
    state_mod.mark_seen(st, "msg-1")
    state_mod.save_state(st, tmp_path)

    # simulate a fresh process restarting and reloading state
    fresh = state_mod.load_state(tmp_path)
    assert state_mod.is_seen(fresh, "msg-1")


def test_single_instance_lock_blocks_second_holder(tmp_path):
    lock1 = SingleInstanceLock(base=tmp_path)
    with lock1:
        lock2 = SingleInstanceLock(base=tmp_path)
        try:
            with lock2:
                assert False, "second lock should not have been acquired"
        except AlreadyRunningError:
            pass

    # lock released -- a new acquire now succeeds
    with SingleInstanceLock(base=tmp_path):
        pass
