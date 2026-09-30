"""Production entry points for scheduled scans/briefs.

TWO DEPLOYMENT SHAPES:

1. Single-process (``run``) -- for manual/ad-hoc use only, by whoever
   directly holds the OAuth credential. NOT for a scheduled coding agent
   once isolation is deployed, since it requires direct token access.

     python3 -m omnissa_agent.cli run --kind scan --client-secret <path> --token <path>
     python3 -m omnissa_agent.cli run --kind brief --client-secret <path> --token <path>

2. Privilege-separated (``ingest`` + ``classify``) -- THE SHAPE FOR
   SCHEDULED PRODUCTION. ``ingest`` runs only as the dedicated,
   credential-owning identity (a systemd timer, never a coding agent) and
   writes a restricted, sanitized JSON drop file. ``classify`` runs as
   the scheduled coding agent's own identity, never touches Gmail or a
   token, and reads that drop file:

     # privileged identity only, e.g. via systemd timer:
     python3 -m omnissa_agent.cli ingest --client-secret <path> --token <path> --out /var/lib/omnissa-agent/drop/latest-scan.json

     # the scheduled coding agent, unprivileged:
     python3 -m omnissa_agent.cli classify --kind scan --ingest-result /var/lib/omnissa-agent/drop/latest-scan.json

Both shapes do their OWN account/label verification at runtime (refresh
the OAuth token, call Gmail, check the profile email and the exact
``Archive_/@omnissa.com`` label -- see gmail_ingest.py) and only proceed
if that verification passes. ``classify`` never accepts an
externally-asserted "VERIFIED" string on the command line -- the status
comes from the drop file's own ``status`` field, which only the
privileged ``ingest`` identity can write (enforced by filesystem
ownership/permissions in the isolated deployment, not by this code
alone -- see docs/gmail-ingestion-security-review.md).

``scan``/``brief`` (no live Gmail call) remain for OFFLINE/SYNTHETIC
TESTING AND MANUAL REPLAY ONLY -- they take a pre-built messages JSON
file and a caller-supplied ``--email-status`` string. Never point them
at a real, unverified fetch: nothing here checks that the JSON actually
came from a verified source. Use ``run`` for anything live.

Exit codes (a scheduler should branch on these, not on stdout text):
  0  ran normally, COMPLETE (new findings or none -- both are success)
  2  REFUSED: boundary/account/label check failed (or, for scan/brief,
     --email-status did not start with "VERIFIED"); nothing processed
  3  ran, but the LLM summary step was deferred (every omniroute backend
     failed) -- classification + brief still completed and were written
  4  unexpected error (bad input, bad client-secret file, etc.) --
     nothing partially written for that run
  5  RATE_LIMITED: Gmail itself is rate-limiting this account -- retry
     later, this is not a configuration problem
  6  PARTIAL: the ingestion deadline was reached before every available
     message was fetched. This is NOT the same as exit 0 -- do not treat
     it as a normal success in monitoring/alerting. Data collected so
     far is genuine and was written; unprocessed message ids were never
     marked as seen, so the next run picks them up rather than silently
     skipping them. The brief itself also says "VERIFIED (PARTIAL --
     ingestion deadline reached)" so this is visible in the report text
     too, not only in the exit code.
  7  LOCKED: another instance already held this state dir's lock (scan
     and brief share one checkpoint per identity) -- this run was
     skipped cleanly rather than racing it. Not a failure; the next
     scheduled run tries again normally.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path

from . import focus as focus_mod
from . import gmail_ingest, google_oauth, research, router
from . import lock as lock_mod
from . import state as state_mod
from .agent_a import Finding
from .agent_b import draft_from_finding
from .gmail_api import GmailReadonlyClient
from .pipeline import run_pilot
from .sources import GmailMessage, StaticMessageSource

REFUSED = 2
LLM_DEFERRED = 3
UNEXPECTED_ERROR = 4
RATE_LIMITED = 5
PARTIAL = 6  # ingestion deadline reached -- data is real but incomplete, not a failure
LOCKED = 7  # another instance already holds this state dir's lock -- skipped, not a failure

_INGEST_STATUS_TO_EXIT_CODE = {
    gmail_ingest.IngestStatus.AUTH_FAILURE: REFUSED,
    gmail_ingest.IngestStatus.WRONG_ACCOUNT: REFUSED,
    gmail_ingest.IngestStatus.LABEL_MISSING: REFUSED,
    gmail_ingest.IngestStatus.LABEL_AMBIGUOUS: REFUSED,
    gmail_ingest.IngestStatus.RATE_LIMITED: RATE_LIMITED,
    gmail_ingest.IngestStatus.UNEXPECTED_ERROR: UNEXPECTED_ERROR,
}


def _is_verified(email_status: str) -> bool:
    return email_status.strip().upper().startswith("VERIFIED")


def _messages_from_json(raw: str) -> list[GmailMessage]:
    items = json.loads(raw) if raw.strip() else []
    return [
        GmailMessage(
            id=m["id"],
            subject=m["subject"],
            snippet=m["snippet"],
            sender=m["sender"],
            date=m["date"],
            label_ids=tuple(m.get("label_ids", [])),
        )
        for m in items
    ]


def _write_report(kind: str, report, *, state_base=None, file_mode: int = 0o600, reports_dir=None) -> Path:
    """``file_mode`` defaults to owner-only (0600) for manual/single-process
    use. The privilege-separated deployment passes 0640 so a dedicated
    reader group (never ``georjero`` writing, only reading) can see the
    output -- the directory's own setgid bit (set by the deploy script,
    not here) makes new files inherit that group automatically.

    ``reports_dir``, when given, overrides the default ``<state>/reports``
    location entirely. This matters for the privilege-separated
    deployment: a reports directory nested under a 0700 identity home
    (e.g. ``/var/lib/omnissa-analysis/state/reports``) is unreachable by
    any reader group no matter its OWN permissions, because every
    ancestor directory needs traversal (+x) rights too -- confirmed live
    (2026-09-30): ``/var/lib/omnissa-analysis`` itself is
    ``750 omnissa-analysis:omnissa-analysis``, which blocks everyone
    else regardless of what ``reports/`` underneath it allows. The fix
    is a reports path whose entire ancestor chain is already traversable
    -- e.g. a sibling of the drop directory under the world-traversable
    ``/var/lib/omnissa-agent/``, not nested inside a private home.
    """
    reports_dir = Path(reports_dir) if reports_dir else (state_mod.state_dir(state_base) / "reports")
    reports_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = reports_dir / f"{ts}-{kind}.md"
    text = report.brief_markdown
    if report.drafts:
        text += "\n\n## Drafts (local only -- DRAFT ONLY, NOT SENT)\n"
        for d in report.drafts:
            text += (
                f"\n### {d.subject}\nTo: {d.to}\n\n{d.body}\n\n"
                f"Confirm before sending: {d.confirm_before_sending}\n"
            )
    path.write_text(text)
    path.chmod(file_mode)
    return path


def _run(args, *, kind: str, use_llm: bool) -> int:
    if not _is_verified(args.email_status):
        print(
            f"REFUSED: --email-status {args.email_status!r} is not VERIFIED -- "
            "no messages read, no mailbox search performed.",
            file=sys.stderr,
        )
        return REFUSED

    raw = sys.stdin.read() if args.messages == "-" else Path(args.messages).read_text()
    try:
        messages = _messages_from_json(raw)
    except Exception as exc:
        print(f"ERROR: malformed --messages input: {exc}", file=sys.stderr)
        return UNEXPECTED_ERROR

    state_base = Path(args.state_dir) if args.state_dir else None
    report = run_pilot(
        source=StaticMessageSource(messages),
        email_status=args.email_status,
        use_llm=use_llm,
        max_messages=args.max_messages,
        state_base=state_base,
    )
    path = _write_report(kind, report, state_base=state_base)
    print(f"wrote {path}")
    print(report.brief_markdown)

    if report.llm_deferred:
        print("LLM summary deferred (all omniroute backends failed this run)", file=sys.stderr)
        return LLM_DEFERRED
    return 0


def _authorize(args) -> int:
    """ONE-TIME interactive handoff. Prints a URL; the OPERATOR opens it in
    their own browser and signs in personally -- this code never does
    that itself. Blocks until the redirect lands, then saves the token.
    """
    try:
        client_config = google_oauth.load_client_config(Path(args.client_secret))
    except Exception as exc:
        print(f"ERROR: cannot load --client-secret: {exc}", file=sys.stderr)
        return UNEXPECTED_ERROR

    pkce = google_oauth.new_pkce_pair()
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://127.0.0.1:{args.port}/"
    url = google_oauth.build_authorization_url(
        client_config, redirect_uri=redirect_uri, state=state, pkce=pkce
    )

    print("=" * 70)
    print("Open this URL in YOUR OWN browser and sign in as george@gjh-inc.com.")
    print("If this shell is on a remote host, first tunnel the port from")
    print("wherever your browser runs, e.g.:")
    print(f"  ssh -N -L {args.port}:127.0.0.1:{args.port} <user>@<this-host>")
    print("=" * 70)
    print(url)
    print("=" * 70, flush=True)
    print(
        f"Waiting up to {args.timeout}s for the redirect on 127.0.0.1:{args.port} ...",
        file=sys.stderr,
        flush=True,
    )

    try:
        code = google_oauth.run_loopback_and_get_code(
            port=args.port, expected_state=state, timeout_s=args.timeout
        )
    except google_oauth.OAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return UNEXPECTED_ERROR

    try:
        token = google_oauth.exchange_code_for_tokens(
            client_config, code=code, redirect_uri=redirect_uri, pkce=pkce
        )
    except google_oauth.OAuthError as exc:
        print(f"ERROR: token exchange failed: {exc}", file=sys.stderr)
        return UNEXPECTED_ERROR

    google_oauth.save_token_file(Path(args.token), token)
    print(f"Saved token to {args.token} (mode 600). `run` will use it from now on.")
    return 0


def _list_labels(args) -> int:
    """Read-only diagnostic: verify account, then print label NAMES only
    (never message content) so a human can pick the exact string for
    ``gmail_ingest.EXPECTED_LABEL_NAME``. No messages.list/get call is made.
    """
    try:
        client_config = google_oauth.load_client_config(Path(args.client_secret))
        access_token = google_oauth.refresh_access_token(client_config, Path(args.token))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return UNEXPECTED_ERROR

    client = GmailReadonlyClient(access_token)
    try:
        account = gmail_ingest.verify_account(client)
    except gmail_ingest.IngestionRefused as exc:
        print(f"{exc.status.value}: {exc.reason}", file=sys.stderr)
        return _INGEST_STATUS_TO_EXIT_CODE[exc.status]

    labels = client.list_labels()
    names = sorted(l.get("name", "") for l in labels if l.get("type") == "user")
    print(f"account={account}")
    print(f"user labels ({len(names)}):")
    for name in names:
        marker = "  <-- contains 'omnissa'" if "omnissa" in name.lower() else ""
        print(f"  {name!r}{marker}")
    return 0


def _reconcile(args) -> int:
    """READ-ONLY recovery diagnostic: compare the ingestion checkpoint's
    ``gmail_ingested_ids`` against the analysis checkpoint's ``seen_ids``
    and report exactly which ids were fetched but never classified.
    Prints ids only (never subjects/snippets/content) -- ids are opaque
    Gmail identifiers, not message content. Makes NO changes to either
    checkpoint; see ``requeue`` for the actual recovery action.
    """
    ingest_state = state_mod.load_state(Path(args.ingest_state_dir) if args.ingest_state_dir else None)
    analysis_state = state_mod.load_state(Path(args.analysis_state_dir) if args.analysis_state_dir else None)

    fetched = set(ingest_state.get("gmail_ingested_ids", []))
    classified = set(analysis_state.get("seen_ids", []))
    pending = fetched - classified

    print(f"fetched={len(fetched)} classified={len(classified)} pending={len(pending)}")
    if pending:
        print("pending ids (fetched but never classified -- candidates for `requeue`):")
        for mid in sorted(pending):
            print(f"  {mid}")
    return 0


def _requeue(args) -> int:
    """Targeted recovery: remove specific ids from a checkpoint's id list
    (default key ``gmail_ingested_ids``) so the next `ingest` run
    re-fetches exactly those messages from Gmail -- NOT a blanket
    checkpoint wipe. Backs up the checkpoint file (timestamped, full
    copy) to ``--backup-dir`` BEFORE making any change. Nothing is
    fetched here; this only edits local bookkeeping.

    ``--verify-unclassified-against`` is an optional safety cross-check:
    given the ANALYSIS checkpoint's own state dir, refuses to requeue any
    id that's already present in its ``seen_ids`` -- i.e. genuinely
    already classified, not actually part of the backlog. Without this
    flag, requeue trusts the caller's id list as-is (it only knows about
    the ONE checkpoint named by ``--state-dir``; it never reads the
    other identity's private state unless explicitly told to and given
    read access to it -- e.g. by being run as root).
    """
    state_base = Path(args.state_dir) if args.state_dir else None
    checkpoint_path = state_mod.state_dir(state_base) / "checkpoint.json"
    if not checkpoint_path.exists():
        print(f"ERROR: no checkpoint at {checkpoint_path}", file=sys.stderr)
        return UNEXPECTED_ERROR

    if args.ids_file:
        ids_to_remove = [
            line.strip() for line in Path(args.ids_file).read_text().splitlines() if line.strip()
        ]
    else:
        ids_to_remove = [i.strip() for i in args.ids.split(",") if i.strip()]
    if not ids_to_remove:
        print("ERROR: no ids given (--ids or --ids-file)", file=sys.stderr)
        return UNEXPECTED_ERROR

    if args.verify_unclassified_against:
        analysis_state = state_mod.load_state(Path(args.verify_unclassified_against))
        already_classified = set(analysis_state.get("seen_ids", [])) & set(ids_to_remove)
        if already_classified:
            print(
                f"REFUSED: {len(already_classified)} requested id(s) are already classified "
                f"(present in seen_ids) -- not genuinely part of the backlog, refusing to "
                f"requeue them: {sorted(already_classified)}",
                file=sys.stderr,
            )
            return REFUSED

    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_path = backup_dir / f"checkpoint-{ts}.json.bak"
    backup_path.write_text(checkpoint_path.read_text())
    backup_path.chmod(0o600)
    print(f"backed up {checkpoint_path} to {backup_path} before making any change")

    st = state_mod.load_state(state_base)
    key = args.key
    before = list(st.get(key, []))
    ids_to_remove_set = set(ids_to_remove)
    after = [i for i in before if i not in ids_to_remove_set]
    removed = len(before) - len(after)
    st[key] = after
    state_mod.save_state(st, state_base)

    print(f"key={key!r}: removed {removed} of {len(ids_to_remove)} requested id(s), {len(after)} remain")
    if removed < len(ids_to_remove):
        missing = ids_to_remove_set - set(before)
        print(f"NOTE: {len(missing)} requested id(s) were not present in {key!r} (already absent): {sorted(missing)}", file=sys.stderr)
    return 0


def _research_demo(args) -> int:
    """Offline, sample-data-only demonstration of the Stage 2 research
    brief shape -- see research.py's module docstring. No network, no
    credential, never scheduled; not the real classify pipeline's
    output, and never mixed with it.
    """
    text = research.render_research_brief(research.sample_opportunities(), focus_category=args.focus)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def _draft(args) -> int:
    """The ONLY path that ever produces a real (non-synthetic) Agent B
    draft. A human types the summary and picks the confidence themselves
    -- nothing here extracts or infers a claim from raw message content.
    Confirms this is a QUESTION, not a claimed benefit; writes DRAFT ONLY
    -- NOT SENT to a local file. No Gmail write tool exists to call.
    """
    finding = Finding(
        message_id=args.message_id,
        category=args.category,
        confidence=args.confidence,
        summary=args.summary,
        source_ref=args.source_ref,
    )
    draft = draft_from_finding(finding)
    if draft is None:
        print(
            f"No draft produced: confidence {args.confidence!r} does not meet the "
            "Confirmed/Likely bar for drafting.",
            file=sys.stderr,
        )
        return REFUSED

    state_base = Path(args.state_dir) if args.state_dir else None
    reports_dir = state_mod.state_dir(state_base) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = reports_dir / f"{ts}-draft-{args.message_id}.md"
    text = (
        f"### {draft.subject}\nTo: {draft.to}\n\n{draft.body}\n\n"
        f"Confirm before sending: {draft.confirm_before_sending}\n\n"
        f"Human-provided evidence note: {args.note}\n"
    )
    path.write_text(text)
    path.chmod(0o600)
    print(f"wrote {path}")
    print(text)
    return 0


def _pipeline_from_ingest_result(
    ingest_result, *, kind: str, args, quiet_content: bool = False, focus_category: str | None = None
) -> int:
    """Shared back half: a verified IngestionResult -> Agent A/B -> report.

    Used by both ``run`` (single-process, credential-holding) and
    ``classify`` (privilege-separated: reads a drop file written by a
    separate, credential-holding identity -- never touches Gmail itself).

    ``quiet_content``, set by ``classify`` only: ``run``/``scan``/``brief``
    are documented manual/ad-hoc commands -- a human typed them and is
    looking at their own terminal, so printing the brief there is exactly
    the point. ``classify`` is the actual scheduled production path,
    invoked unattended by a systemd service whose stdout/stderr go
    straight into the system journal -- readable by anyone in
    `adm`/`systemd-journal`, a broader audience than the dedicated
    `omnissa-reports-readers` group that gates the report FILE itself.
    Confirmed live (2026-09-30): real subject lines from a scheduled run
    ended up in journalctl output that then had to be pasted for
    diagnosis, directly violating the standing rule that message
    content never leaves the operator's own terminal. The report file
    (written either way, below) remains the correct, access-controlled
    place for this content -- this only changes what reaches this
    process's own stdout/stderr.
    """
    if ingest_result.deadline_hit:
        print("NOTE: ingestion deadline reached -- partial result this run", file=sys.stderr)

    if ingest_result.status != gmail_ingest.IngestStatus.OK:
        print(f"{ingest_result.status.value}: {ingest_result.reason}", file=sys.stderr)
        return _INGEST_STATUS_TO_EXIT_CODE[ingest_result.status]

    status_word = "VERIFIED (PARTIAL -- ingestion deadline reached)" if ingest_result.deadline_hit else "VERIFIED"
    email_status = (
        f"{status_word} account={ingest_result.account} label_id={ingest_result.label_id} "
        f"label_name={ingest_result.label_name} (runtime-checked by gmail_ingest.run_ingestion)"
    )
    llm_combo = router.LOCAL_ONLY_COMBO if args.llm_policy == "local-only" else router.DEFAULT_COMBO
    state_base = Path(args.state_dir) if args.state_dir else None

    report = run_pilot(
        source=StaticMessageSource(ingest_result.messages),
        email_status=email_status,
        use_llm=(kind == "brief"),
        llm_combo=llm_combo,
        focus_category=focus_category,
        max_messages=args.max_messages,
        state_base=state_base,
    )
    if report.email_status == "SKIPPED_ALREADY_RUNNING":
        # run_pilot's OWN lock (held since before the ingest/classify
        # split) was already held by a concurrent run on this state dir.
        # Nothing was classified -- write no report, and critically,
        # tell the caller so it does NOT treat the input as consumed.
        # Confirmed live (2026-09-30) as a real gap: classify's
        # delete-on-consume previously fired on ANY ingest_result.status
        # == OK, blind to whether run_pilot actually ran -- a lock-skip
        # would otherwise delete real, never-processed data, recreating
        # the original incident in a new place.
        print("LOCKED: another classify run already holds this state dir's lock -- "
              "skipping this cycle cleanly; input left untouched for the next run.",
              file=sys.stderr)
        return LOCKED
    # The drop file can legitimately hold MORE messages than one run
    # processes -- either ingest's own deadline was hit, or (confirmed
    # live, 2026-09-29, a real near-miss) run_pilot's own --max-messages
    # cap truncates `ingest_result.messages` BEFORE Agent A ever sees the
    # rest: a merged drop of 83 messages (33 old + 50 new after
    # merge_pending) with the default cap of 50 silently classified only
    # the first 50 -- 33 real, never-before-classified messages were
    # never looked at, yet nothing here distinguished that from a clean
    # success. `report.messages_seen` reflects what run_pilot actually
    # processed after its own cap; `len(ingest_result.messages)` is the
    # drop's raw, untruncated size -- if they differ, real data was left
    # unprocessed this run.
    message_list_truncated = len(ingest_result.messages) > report.messages_seen
    partial = ingest_result.deadline_hit or message_list_truncated

    file_mode = 0o640 if getattr(args, "report_group_readable", False) else 0o600
    reports_dir = getattr(args, "reports_dir", None)
    path = _write_report(kind, report, state_base=state_base, file_mode=file_mode, reports_dir=reports_dir)
    print(f"wrote {path}")
    print(
        f"account={ingest_result.account} label={ingest_result.label_name} "
        f"drop_size={len(ingest_result.messages)} "
        f"messages_processed={report.messages_seen} "
        f"duplicates_skipped={ingest_result.duplicates_skipped} "
        f"rejected_stale_label={len(ingest_result.rejected_stale_label_ids)} "
        f"malformed={len(ingest_result.malformed_ids)} "
        f"deadline_hit={ingest_result.deadline_hit} "
        f"message_list_truncated={message_list_truncated}"
    )
    if quiet_content:
        # focus_category is a small, fixed-vocabulary category name (e.g.
        # "Renewal"), never message content -- safe to log even here.
        print(f"findings={report.findings_count} drafts={report.drafts_count} focus={focus_category or 'none'}")
    else:
        print(report.brief_markdown)
    if report.llm_backend:
        print(f"LLM backend used: {report.llm_backend}", file=sys.stderr)

    if partial:
        reasons = []
        if ingest_result.deadline_hit:
            reasons.append("ingestion deadline reached")
        if message_list_truncated:
            reasons.append(
                f"drop held {len(ingest_result.messages)} messages but only "
                f"{report.messages_seen} were processed this run (--max-messages cap)"
            )
        print(
            "PARTIAL: " + "; ".join(reasons) + " -- not all available messages were "
            "processed this run. Unprocessed messages are NOT marked seen and will "
            "be picked up on the next run, not silently dropped.",
            file=sys.stderr,
        )
        return PARTIAL
    if report.llm_deferred:
        print("LLM summary deferred (all omniroute backends failed this run)", file=sys.stderr)
        return LLM_DEFERRED
    return 0


def _run_live(args, *, kind: str) -> int:
    """Single-process live path: refresh token -> verify -> ingest -> pipeline,
    all as whatever identity runs this command. Requires direct credential
    access -- fine for manual/ad-hoc use, but this is exactly the identity
    that must NOT be the same as the scheduled coding agent once isolation
    is deployed. See ``ingest`` + ``classify`` for the privilege-separated
    split used by the actual scheduled jobs.
    """
    try:
        client_config = google_oauth.load_client_config(Path(args.client_secret))
    except Exception as exc:
        print(f"ERROR: cannot load --client-secret: {exc}", file=sys.stderr)
        return UNEXPECTED_ERROR

    try:
        access_token = google_oauth.refresh_access_token(client_config, Path(args.token))
    except google_oauth.OAuthError as exc:
        print(f"REFUSED: token refresh failed: {exc}", file=sys.stderr)
        return REFUSED

    state_base = Path(args.state_dir) if args.state_dir else None
    gmail_client = GmailReadonlyClient(access_token)
    ingest_result = gmail_ingest.run_ingestion(
        gmail_client,
        max_pages=args.max_pages,
        max_messages=args.max_messages,
        deadline_s=args.deadline,
        state_base=state_base,
    )
    # Best-effort, non-fatal (see focus.resolve_focus_instruction): this
    # single-process path already holds the same read-only Gmail client
    # ingest/classify would use separately, so it can check for a focus
    # instruction directly -- no new flag needed here.
    instruction = focus_mod.resolve_focus_instruction(gmail_client)
    focus_category = instruction.category if instruction else None
    return _pipeline_from_ingest_result(ingest_result, kind=kind, args=args, focus_category=focus_category)


def _ingest(args) -> int:
    """PRIVILEGED-SIDE entry point: the ONLY command that ever touches an
    OAuth token or a Gmail API. Never invoked by a coding agent in the
    isolated deployment -- only by the dedicated ingestion identity's own
    systemd timer. Writes a restricted, sanitized JSON drop file (ids/
    subject/snippet/sender/date/labels -- never a token, never a raw
    Gmail API response) atomically, then exits. No classification, no
    OmniRoute call, no LLM -- this process never needs network access to
    anything but accounts.google.com / gmail.googleapis.com.
    """
    try:
        client_config = google_oauth.load_client_config(Path(args.client_secret))
        access_token = google_oauth.refresh_access_token(client_config, Path(args.token))
    except google_oauth.OAuthError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return REFUSED
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return UNEXPECTED_ERROR

    state_base = Path(args.state_dir) if args.state_dir else None

    # scan and brief ingest share the SAME --state-dir (and therefore the
    # same checkpoint.json) on this identity. Without a lock, an overlap
    # (a manual trigger colliding with a scheduled run, or a slow run
    # running past the next cycle) is a real read-modify-write race on
    # that shared file AND on the shared drop file's merge -- the exact
    # same class of bug as the original drop-overwrite incident, just
    # via concurrency instead of sequencing. Confirmed missing (2026-09-30):
    # cli.py never called into lock.py at all after the ingest/classify
    # split, even though lock.py itself was built and tested earlier.
    try:
        with lock_mod.SingleInstanceLock(base=state_base):
            return _ingest_locked(args, client_config, access_token, state_base)
    except lock_mod.AlreadyRunningError:
        print(
            "LOCKED: another ingest run already holds this state dir's lock -- "
            "skipping this cycle cleanly rather than racing it.",
            file=sys.stderr,
        )
        return LOCKED


def _ingest_locked(args, client_config, access_token, state_base) -> int:
    gmail_client = GmailReadonlyClient(access_token)
    ingest_result = gmail_ingest.run_ingestion(
        gmail_client,
        max_pages=args.max_pages,
        max_messages=args.max_messages,
        deadline_s=args.deadline,
        state_base=state_base,
    )

    # Optional, best-effort focus handoff to classify -- see focus.py.
    # Re-resolved fresh EVERY run (never cached beyond one cycle): an
    # operator changes focus by labeling a new message, and a stale one
    # ages out on its own via focus.FOCUS_MAX_AGE_HOURS. Always
    # overwritten (never merged) -- unlike the message drop file, there
    # is nothing here to lose by replacing it outright each run.
    focus_out = getattr(args, "focus_out", None)
    if focus_out:
        focus_path = Path(focus_out)
        instruction = focus_mod.resolve_focus_instruction(gmail_client)
        if instruction is None:
            focus_path.unlink(missing_ok=True)
        else:
            focus_path.parent.mkdir(parents=True, exist_ok=True)
            focus_tmp = focus_path.with_suffix(".tmp")
            focus_tmp.write_text(json.dumps(instruction.to_json_dict()))
            focus_tmp.chmod(0o640)
            focus_tmp.replace(focus_path)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # MERGE with, never overwrite, an unconsumed prior drop -- see
    # gmail_ingest.merge_pending's docstring for the real incident this
    # fixes. A pending file only exists if a previous `classify` run
    # never got to (or never finished) consuming it; `classify` deletes
    # the file on successful consumption, so its mere presence here IS
    # the "not yet consumed" signal -- no separate marker needed.
    if out_path.exists():
        try:
            prior = gmail_ingest.IngestionResult.from_json_dict(json.loads(out_path.read_text()))
            ingest_result = gmail_ingest.merge_pending(prior, ingest_result)
        except Exception as exc:
            print(f"WARNING: could not read prior pending drop file, not merging: {exc}", file=sys.stderr)

    tmp_path = out_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(ingest_result.to_json_dict(), indent=2))
    tmp_path.chmod(0o640)  # owner rw, group r -- the narrow one-way handoff
    tmp_path.replace(out_path)  # atomic -- a reader never sees a partial file

    status_label = "PARTIAL" if ingest_result.deadline_hit else ingest_result.status.value
    print(
        f"status={status_label} account={ingest_result.account} "
        f"messages={len(ingest_result.messages)} wrote={out_path}"
    )
    if ingest_result.status == gmail_ingest.IngestStatus.OK and ingest_result.deadline_hit:
        print(
            "PARTIAL: deadline reached before all messages were fetched -- "
            "unprocessed ids were NOT marked seen, next run will retry them.",
            file=sys.stderr,
        )
        return PARTIAL
    return _INGEST_STATUS_TO_EXIT_CODE.get(ingest_result.status, 0)


def _classify(args) -> int:
    """UNPRIVILEGED-SIDE entry point: the ONLY command a scheduled coding
    agent should ever call for real data. Never touches Gmail, never
    holds a credential -- reads a drop file that only the privileged
    ``ingest`` identity can write, and derives VERIFIED/BLOCKED from that
    file's own ``status`` field. There is no ``--email-status`` flag here
    for a caller to assert; the file's provenance (its restrictive
    ownership/permissions, set by ``ingest``) is what makes it trustworthy,
    not a claim typed on this command line.
    """
    in_path = Path(args.ingest_result)
    try:
        data = json.loads(in_path.read_text())
        ingest_result = gmail_ingest.IngestionResult.from_json_dict(data)
    except Exception as exc:
        print(f"ERROR: cannot read --ingest-result {in_path}: {exc}", file=sys.stderr)
        return UNEXPECTED_ERROR

    if args.max_age_s is not None:
        age = time.time() - in_path.stat().st_mtime
        if age > args.max_age_s:
            print(
                f"REFUSED: {in_path} is {age:.0f}s old, older than --max-age-s "
                f"{args.max_age_s:.0f}s -- the privileged ingest side may have "
                "stopped running; not processing stale data as if fresh.",
                file=sys.stderr,
            )
            return REFUSED

    # Optional focus instruction, written by ingest (see focus.py). Only
    # a small, fixed-vocabulary category name ever crosses this
    # boundary -- never message content. Any problem reading it (file
    # missing, corrupt, unknown category) is silently treated as "no
    # focus this run" -- exactly the same fail-open-to-normal-brief
    # behavior ingest itself uses when resolving the instruction live.
    focus_category = None
    focus_in = getattr(args, "focus_in", None)
    if focus_in and Path(focus_in).exists():
        try:
            focus_data = json.loads(Path(focus_in).read_text())
            instruction = focus_mod.FocusInstruction.from_json_dict(focus_data)
        except Exception:
            instruction = None
        if instruction is not None:
            focus_category = instruction.category

    # scan and brief classify share the SAME --state-dir (and therefore
    # the same checkpoint.json) on the analysis identity. This is
    # ALREADY locked -- pipeline.run_pilot (called via
    # _pipeline_from_ingest_result below) has wrapped its body in
    # SingleInstanceLock(base=state_base) since long before the
    # ingest/classify split. Do NOT add a second lock on the same file
    # here: flock is per-open-file-description, so a second open+flock
    # on the same path from this same process would see run_pilot's own
    # lock as "already held" and self-block on every single run --
    # confirmed live (2026-09-30) as a real regression while adding
    # locking to `ingest`, which had no equivalent existing protection.
    rc = _pipeline_from_ingest_result(
        ingest_result, kind=args.kind, args=args, quiet_content=True, focus_category=focus_category
    )

    # Delete the drop file ONLY once every message it held has actually
    # been classified. Checked independently of `rc` here (not just
    # rc != LOCKED) -- confirmed live (2026-09-29) as a real near-miss:
    # `_pipeline_from_ingest_result`'s own --max-messages cap can
    # truncate the message list it actually processes to fewer than
    # `len(ingest_result.messages)`, and that used to delete the file
    # anyway since status was OK and rc wasn't LOCKED. A deadline_hit-only
    # PARTIAL (no truncation) still deletes safely -- everything ingest
    # DID fetch was fully processed, only Gmail itself has more unfetched
    # mail, which is a separate, already-tracked situation.
    message_list_truncated = len(ingest_result.messages) > args.max_messages
    if (
        ingest_result.status == gmail_ingest.IngestStatus.OK
        and rc != LOCKED
        and not message_list_truncated
    ):
        try:
            in_path.unlink()
        except OSError as exc:
            print(f"WARNING: classified successfully but could not remove {in_path}: {exc}", file=sys.stderr)
    elif message_list_truncated:
        print(
            f"NOTE: drop file left in place -- it held {len(ingest_result.messages)} "
            f"messages, more than --max-messages {args.max_messages}, so not everything "
            "in it was processed this run.",
            file=sys.stderr,
        )

    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omnissa_agent.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--email-status",
        required=True,
        help='Result of the caller\'s own gmail_boundary check, e.g. '
        '"VERIFIED account=... label_id=..." or "BLOCKED: <reason>"',
    )
    common.add_argument(
        "--messages",
        default="-",
        help="Path to a JSON array of pre-fetched messages, or '-' for stdin",
    )
    common.add_argument("--max-messages", type=int, default=50)
    common.add_argument(
        "--state-dir",
        default=None,
        help="Override checkpoint/lock/report location (default: ~/.local/state/omnissa-agent)",
    )

    sub.add_parser(
        "scan", parents=[common], help="OFFLINE/SYNTHETIC ONLY -- no live Gmail call, no LLM"
    )
    sub.add_parser(
        "brief", parents=[common], help="OFFLINE/SYNTHETIC ONLY -- no live Gmail call, LLM summary"
    )

    auth_p = sub.add_parser(
        "authorize", help="ONE-TIME interactive OAuth handoff -- prints a URL for YOU to open"
    )
    auth_p.add_argument("--client-secret", required=True)
    auth_p.add_argument("--token", required=True)
    auth_p.add_argument("--port", type=int, default=8765)
    auth_p.add_argument("--timeout", type=int, default=300)

    draft_p = sub.add_parser(
        "draft",
        help="produce ONE real, human-confirmed local draft (no Gmail write capability exists)",
    )
    draft_p.add_argument("--message-id", required=True, help="the real Gmail message id this concerns")
    draft_p.add_argument("--category", required=True)
    draft_p.add_argument(
        "--summary",
        required=True,
        help="YOUR OWN summary of the situation -- never auto-extracted from the message",
    )
    draft_p.add_argument("--source-ref", required=True, help="e.g. gmail:<id> or an official URL")
    draft_p.add_argument("--confidence", choices=["Confirmed", "Likely", "Unverified"], required=True)
    draft_p.add_argument(
        "--note",
        required=True,
        help="why this is evidence-backed -- e.g. what official source you checked",
    )
    draft_p.add_argument("--state-dir", default=None)

    labels_p = sub.add_parser(
        "list-labels", help="READ-ONLY diagnostic: verify account, print label NAMES only"
    )
    labels_p.add_argument("--client-secret", required=True)
    labels_p.add_argument("--token", required=True)

    reconcile_p = sub.add_parser(
        "reconcile",
        help="READ-ONLY: compare ingest vs analysis checkpoints, report fetched/classified/pending ids",
    )
    reconcile_p.add_argument("--ingest-state-dir", default=None)
    reconcile_p.add_argument("--analysis-state-dir", default=None)

    requeue_p = sub.add_parser(
        "requeue",
        help="Targeted recovery: remove specific ids from a checkpoint so the next ingest re-fetches them",
    )
    requeue_p.add_argument("--state-dir", default=None, help="the checkpoint's own state dir (e.g. omnissa-ingest's)")
    requeue_p.add_argument("--key", default="gmail_ingested_ids", help="checkpoint list key to edit")
    requeue_p.add_argument("--ids", default="", help="comma-separated ids to remove")
    requeue_p.add_argument("--ids-file", default=None, help="path to a file with one id per line")
    requeue_p.add_argument("--backup-dir", required=True, help="where to write a timestamped checkpoint backup first")
    requeue_p.add_argument(
        "--verify-unclassified-against",
        default=None,
        help="path to the ANALYSIS checkpoint's state dir -- refuses to requeue any id already "
        "present in its seen_ids (genuinely already classified). Requires read access to that "
        "identity's private state (e.g. run this command as root).",
    )

    run_p = sub.add_parser("run", help="THE LIVE PATH: real Gmail ingestion -> pipeline")
    run_p.add_argument("--kind", choices=["scan", "brief"], required=True)
    run_p.add_argument(
        "--client-secret", required=True, help="path to the downloaded Desktop OAuth client JSON"
    )
    run_p.add_argument(
        "--token", required=True, help="path to the token file (created by the interactive handoff)"
    )
    run_p.add_argument("--max-messages", type=int, default=50)
    run_p.add_argument("--max-pages", type=int, default=5)
    run_p.add_argument("--deadline", type=float, default=90.0, help="total ingestion wall-clock budget, seconds")
    run_p.add_argument("--state-dir", default=None)
    run_p.add_argument(
        "--llm-policy",
        choices=["local-only", "combo-continuous"],
        default="local-only",
        help="local-only (default) uses combo-private (Ollama only); "
        "combo-continuous may route real content to third-party free-tier "
        "providers -- only pass this with explicit operator approval",
    )

    ingest_p = sub.add_parser(
        "ingest",
        help="PRIVILEGED SIDE (isolated deployment): fetch+verify only, writes a sanitized drop file",
    )
    ingest_p.add_argument("--client-secret", required=True)
    ingest_p.add_argument("--token", required=True)
    ingest_p.add_argument("--out", required=True, help="path to write the sanitized JSON drop file")
    ingest_p.add_argument("--max-messages", type=int, default=50)
    ingest_p.add_argument("--max-pages", type=int, default=5)
    ingest_p.add_argument("--deadline", type=float, default=90.0)
    ingest_p.add_argument("--state-dir", default=None)
    ingest_p.add_argument(
        "--focus-out",
        default=None,
        help="path to write a resolved focus instruction for classify to read (see focus.py). "
        "Optional -- omitting it means no focus handoff at all, same as today's behavior. "
        "Re-resolved and overwritten fresh every run; never merged like the message drop.",
    )

    classify_p = sub.add_parser(
        "classify",
        help="UNPRIVILEGED SIDE (isolated deployment): the only command a scheduled agent should call",
    )
    classify_p.add_argument("--kind", choices=["scan", "brief"], required=True)
    classify_p.add_argument(
        "--ingest-result", required=True, help="drop file written by a privileged `ingest` run"
    )
    classify_p.add_argument(
        "--max-age-s",
        type=float,
        default=None,
        help="refuse if the drop file is older than this many seconds (missed-run detection)",
    )
    classify_p.add_argument(
        "--max-messages",
        type=int,
        default=500,
        help="cap on how many of the drop file's messages this run processes. Kept well "
        "above ingest's own per-run fetch cap (50) because merge_pending can combine a "
        "prior unconsumed drop with a fresh fetch -- a cap too close to ingest's own would "
        "routinely truncate a merged backlog (confirmed live, 2026-09-29: a merged drop of "
        "83 messages against the old default of 50). Truncation past this cap is now safe "
        "either way -- the drop file is left in place, not deleted -- but a generous cap "
        "means that's a rare fallback, not the normal path.",
    )
    classify_p.add_argument("--state-dir", default=None)
    classify_p.add_argument(
        "--focus-in",
        default=None,
        help="path to a focus instruction written by ingest --focus-out (see focus.py). "
        "Optional -- omitting it (default) means the normal, unmodified brief, exactly "
        "today's behavior. A missing/corrupt/unrecognized file is silently treated the "
        "same as omitting the flag, never an error.",
    )
    classify_p.add_argument(
        "--llm-policy", choices=["local-only", "combo-continuous"], default="local-only"
    )
    classify_p.add_argument(
        "--report-group-readable",
        action="store_true",
        help="write reports mode 0640 instead of 0600 -- for the privilege-separated "
        "deployment, where a dedicated reader group (not the analysis identity's own "
        "group) is meant to see the output. Off by default (owner-only).",
    )
    classify_p.add_argument(
        "--reports-dir",
        default=None,
        help="write reports here instead of <state-dir>/reports -- required for the "
        "privilege-separated deployment: a reports dir nested under a private identity "
        "home is unreachable by any reader group regardless of its own permissions, "
        "since every ancestor directory needs its own traversal rights too. Point this "
        "at a path whose whole ancestor chain is already shared/traversable instead "
        "(e.g. a sibling of the drop directory).",
    )

    demo_p = sub.add_parser(
        "research-demo",
        help="OFFLINE, SAMPLE DATA ONLY -- demonstrates the Stage 2 research-note/contact/"
        "revenue-path brief shape (research.py). No network call, no credential, not "
        "scheduled anywhere -- see docs/autonomous-vision-and-open-decisions.md.",
    )
    demo_p.add_argument("--focus", default=None, help="a category name, to demo reorder-only focus")
    demo_p.add_argument("--out", default=None, help="write to this path instead of stdout")

    args = parser.parse_args(argv)
    if args.command == "scan":
        return _run(args, kind="scan", use_llm=False)
    if args.command == "brief":
        return _run(args, kind="brief", use_llm=True)
    if args.command == "authorize":
        return _authorize(args)
    if args.command == "list-labels":
        return _list_labels(args)
    if args.command == "reconcile":
        return _reconcile(args)
    if args.command == "requeue":
        return _requeue(args)
    if args.command == "draft":
        return _draft(args)
    if args.command == "ingest":
        return _ingest(args)
    if args.command == "classify":
        return _classify(args)
    if args.command == "research-demo":
        return _research_demo(args)
    return _run_live(args, kind=args.kind)


if __name__ == "__main__":
    raise SystemExit(main())
