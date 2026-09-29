from omnissa_agent import state as state_mod
from omnissa_agent.lock import AlreadyRunningError, SingleInstanceLock


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
