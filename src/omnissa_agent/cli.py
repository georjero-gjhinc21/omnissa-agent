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
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path

from . import gmail_ingest, google_oauth, router
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


def _write_report(kind: str, report, *, state_base=None, file_mode: int = 0o600) -> Path:
    """``file_mode`` defaults to owner-only (0600) for manual/single-process
    use. The privilege-separated deployment passes 0640 so a dedicated
    reader group (never ``georjero`` writing, only reading) can see the
    output -- the directory's own setgid bit (set by the deploy script,
    not here) makes new files inherit that group automatically.
    """
    reports_dir = state_mod.state_dir(state_base) / "reports"
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


def _pipeline_from_ingest_result(ingest_result, *, kind: str, args) -> int:
    """Shared back half: a verified IngestionResult -> Agent A/B -> report.

    Used by both ``run`` (single-process, credential-holding) and
    ``classify`` (privilege-separated: reads a drop file written by a
    separate, credential-holding identity -- never touches Gmail itself).
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
        max_messages=args.max_messages,
        state_base=state_base,
    )
    file_mode = 0o640 if getattr(args, "report_group_readable", False) else 0o600
    path = _write_report(kind, report, state_base=state_base, file_mode=file_mode)
    print(f"wrote {path}")
    print(
        f"account={ingest_result.account} label={ingest_result.label_name} "
        f"messages_seen={len(ingest_result.messages)} "
        f"duplicates_skipped={ingest_result.duplicates_skipped} "
        f"rejected_stale_label={len(ingest_result.rejected_stale_label_ids)} "
        f"malformed={len(ingest_result.malformed_ids)} "
        f"deadline_hit={ingest_result.deadline_hit}"
    )
    print(report.brief_markdown)
    if report.llm_backend:
        print(f"LLM backend used: {report.llm_backend}", file=sys.stderr)

    if ingest_result.deadline_hit:
        print(
            "PARTIAL: ingestion deadline reached -- not all available messages were "
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
    return _pipeline_from_ingest_result(ingest_result, kind=kind, args=args)


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
    gmail_client = GmailReadonlyClient(access_token)
    ingest_result = gmail_ingest.run_ingestion(
        gmail_client,
        max_pages=args.max_pages,
        max_messages=args.max_messages,
        deadline_s=args.deadline,
        state_base=state_base,
    )

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

    rc = _pipeline_from_ingest_result(ingest_result, kind=args.kind, args=args)

    # Delete the drop file ONLY once its data has actually been
    # classified (status OK -- a report was written, whether or not the
    # LLM step was deferred or the ingest side was partial). This is
    # what tells the NEXT `ingest` run there is nothing pending to merge
    # with. On a refusal (bad account/label recorded in the file, or
    # unreadable input), the file is left in place for inspection/retry
    # -- never silently discarded.
    if ingest_result.status == gmail_ingest.IngestStatus.OK:
        try:
            in_path.unlink()
        except OSError as exc:
            print(f"WARNING: classified successfully but could not remove {in_path}: {exc}", file=sys.stderr)

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
    classify_p.add_argument("--max-messages", type=int, default=50)
    classify_p.add_argument("--state-dir", default=None)
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

    args = parser.parse_args(argv)
    if args.command == "scan":
        return _run(args, kind="scan", use_llm=False)
    if args.command == "brief":
        return _run(args, kind="brief", use_llm=True)
    if args.command == "authorize":
        return _authorize(args)
    if args.command == "list-labels":
        return _list_labels(args)
    if args.command == "draft":
        return _draft(args)
    if args.command == "ingest":
        return _ingest(args)
    if args.command == "classify":
        return _classify(args)
    return _run_live(args, kind=args.kind)


if __name__ == "__main__":
    raise SystemExit(main())
