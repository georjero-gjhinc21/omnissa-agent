"""End-to-end partner-ops behavior: multi-partner ingest tagging,
per-partner brief sections, and the "no new mail still shows standing
gaps" requirement (definition of done for the 2026-09-30 extension).
"""

import json
from pathlib import Path

from omnissa_agent import cli, gmail_ingest as gi
from omnissa_agent.agent_a import AgentAResult, Finding, build_partner_ops_brief
from omnissa_agent.baseline import Baseline
from omnissa_agent.gmail_api import GmailApiError, GmailAuthError, GmailRateLimitError
from omnissa_agent.sources import GmailMessage


def _fake_client_secret(tmp_path):
    p = tmp_path / "client_secret.json"
    p.write_text('{"installed": {"client_id": "x", "client_secret": "y"}}')
    p.chmod(0o600)
    return p


def _fake_token(tmp_path):
    p = tmp_path / "token.json"
    p.write_text('{"access_token": "at", "refresh_token": "rt"}')
    p.chmod(0o600)
    return p


class FakeClient:
    def __init__(self, *, profile_email="george@gjh-inc.com", labels, messages):
        self._profile_email = profile_email
        self._labels = labels  # {label_name: label_id}
        self._messages = messages  # {label_id: [raw_message, ...]}
        self.get_message_calls = []

    def get_profile(self):
        return {"emailAddress": self._profile_email}

    def list_labels(self):
        return [{"id": v, "name": k} for k, v in self._labels.items()]

    def list_message_ids(self, *, label_id, page_token=None, max_results=25):
        raws = self._messages.get(label_id, [])
        return [m["id"] for m in raws], None

    def get_message_metadata(self, message_id):
        self.get_message_calls.append(message_id)
        for raws in self._messages.values():
            for m in raws:
                if m["id"] == message_id:
                    return m
        raise KeyError(message_id)


def _raw(mid, label_id, subject="Subj"):
    return {
        "id": mid,
        "snippet": "hi",
        "labelIds": [label_id],
        "payload": {"headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": "a@x.com"}, {"name": "Date", "value": "2026-09-29"}]},
    }


def _config(tmp_path, entries):
    text = "partners:\n" + "".join(f'  - id: {pid}\n    label: "{label}"\n' for pid, label in entries)
    p = tmp_path / "partners.yaml"
    p.write_text(text)
    return p


def test_run_ingestion_tags_each_message_with_its_own_partner_id(tmp_path):
    config = _config(tmp_path, [("omnissa", "Archive_/@omnissa.com"), ("microsoft", "Archive_/@microsoft.com")])
    client = FakeClient(
        labels={"Archive_/@omnissa.com": "L1", "Archive_/@microsoft.com": "L2"},
        messages={
            "L1": [_raw("m1", "L1", "Omnissa renewal")],
            "L2": [_raw("m2", "L2", "Microsoft training")],
        },
    )
    result = gi.run_ingestion(client, state_base=tmp_path / "state", partners_config=config)
    assert result.status == gi.IngestStatus.OK
    by_id = {m.id: m.partner_id for m in result.messages}
    assert by_id == {"m1": "omnissa", "m2": "microsoft"}


def test_run_ingestion_skips_a_missing_partner_label_without_refusing_the_whole_run(tmp_path):
    config = _config(tmp_path, [("omnissa", "Archive_/@omnissa.com"), ("nvidia", "Archive_/@nvidia.com")])
    client = FakeClient(
        labels={"Archive_/@omnissa.com": "L1"},  # nvidia label doesn't exist yet
        messages={"L1": [_raw("m1", "L1", "Omnissa item")]},
    )
    result = gi.run_ingestion(client, state_base=tmp_path / "state", partners_config=config)
    assert result.status == gi.IngestStatus.OK
    assert [m.id for m in result.messages] == ["m1"]
    assert "nvidia" in result.skipped_partners
    assert "no label named exactly" in result.skipped_partners["nvidia"]


def test_run_ingestion_refuses_a_label_not_in_the_allowlist_even_if_it_exists(tmp_path):
    """The account's real mailbox might have a label like
    Archive_/@linkedin.com from something else entirely -- it must
    never be fetched just because it happens to exist, only because
    it's in config/partners.yaml."""
    config = _config(tmp_path, [("omnissa", "Archive_/@omnissa.com")])
    client = FakeClient(
        labels={"Archive_/@omnissa.com": "L1", "Archive_/@linkedin.com": "L99"},
        messages={"L1": [_raw("m1", "L1")], "L99": [_raw("m99", "L99")]},
    )
    result = gi.run_ingestion(client, state_base=tmp_path / "state", partners_config=config)
    assert result.status == gi.IngestStatus.OK
    assert [m.id for m in result.messages] == ["m1"]  # linkedin's message never fetched


def test_partner_ops_brief_has_a_section_per_configured_partner_even_with_zero_findings():
    result = AgentAResult(findings=[])
    brief = build_partner_ops_brief(
        result, email_status="VERIFIED",
        partner_ids=["omnissa", "microsoft", "google", "nvidia", "planetbids"],
    )
    for pid in ("omnissa", "microsoft", "google", "nvidia", "planetbids"):
        assert f"## Partner: {pid}" in brief
    assert "no new findings this run" in brief


def test_partner_ops_brief_shows_the_omnissa_gap_even_with_zero_new_messages(tmp_path):
    """Definition of done: a classify report shows the standing Omnissa
    open items (OSP/OTSP, distributors) even when dedup skipped
    everything -- exercised through the real baseline file this time."""
    baseline_dir = tmp_path
    (baseline_dir / "omnissa-baseline.md").write_text(
        "<!-- SCOREBOARD-DATA\n"
        "sourced: true\n"
        "osp_requested: 2\notsp_requested: 2\nosp_completed: 0\notsp_completed: 0\n"
        "distributor_commercial: unknown\ndistributor_healthcare: unknown\n"
        "distributor_public_sector: unknown\ndistributor_deadline: 2026-07-31\n"
        "top_human_action: Complete 2 OSP + 2 OTSP; confirm distributors\n"
        "-->\n"
    )
    from omnissa_agent.baseline import load_partner_baselines

    baselines = load_partner_baselines(baseline_dir, ["omnissa"])
    result = AgentAResult(findings=[])
    brief = build_partner_ops_brief(result, email_status="VERIFIED", partner_ids=["omnissa"], partner_baselines=baselines)
    assert len(brief.encode()) > 323
    assert "## Partner: omnissa" in brief
    assert "requested 2/2, completed 0/0" in brief
    assert "Commercial=unknown" in brief
    assert "Complete 2 OSP + 2 OTSP" in brief


def test_partner_ops_brief_buckets_by_coarse_category_within_a_partner():
    findings = [
        Finding(message_id="m1", category="Renewal", confidence="Unverified", summary="renewal notice", source_ref="gmail:m1", partner_id="omnissa"),
        Finding(message_id="m2", category="RFP", confidence="Unverified", summary="bid notice", source_ref="gmail:m2", partner_id="planetbids"),
        Finding(message_id="m3", category="General", confidence="Unverified", summary="newsletter", source_ref="gmail:m3", partner_id="omnissa"),
    ]
    result = AgentAResult(findings=findings)
    brief = build_partner_ops_brief(result, email_status="VERIFIED", partner_ids=["omnissa", "planetbids"])

    omnissa_section = brief.split("## Partner: omnissa")[1].split("## Partner:")[0]
    assert "### renewal" in omnissa_section
    assert "### noise" in omnissa_section  # General buckets to noise, not shown as its own peer category
    planetbids_section = brief.split("## Partner: planetbids")[1]
    assert "### RFP" in planetbids_section
    assert "bid notice" in planetbids_section
    assert "bid notice" not in omnissa_section  # never leaks into the wrong partner's section


def test_findings_with_no_matching_configured_partner_still_appear_unassigned():
    """A legacy/untagged finding (partner_id="") must never silently
    vanish from the brief just because it doesn't match a configured
    partner -- confirmed live as a real bug found while wiring this up."""
    findings = [Finding(message_id="m1", category="General", confidence="Unverified", summary="untagged item", source_ref="gmail:m1", partner_id="")]
    result = AgentAResult(findings=findings)
    brief = build_partner_ops_brief(result, email_status="VERIFIED", partner_ids=["omnissa"])
    assert "## Partner: (unassigned)" in brief
    assert "untagged item" in brief


def test_cli_classify_shows_omnissa_section_with_zero_new_messages_using_the_real_repo_baseline(tmp_path):
    """The literal definition of done, through the real CLI: classify
    with zero messages in the drop, pointed at the real
    docs/partners/ directory, still produces an Omnissa section with
    the real sourced gap -- and the report is well over 323 bytes."""
    drop = tmp_path / "drop.json"
    empty = gi.IngestionResult(status=gi.IngestStatus.OK, account="george@gjh-inc.com", label_id="", label_name="", messages=[])
    drop.write_text(json.dumps(empty.to_json_dict()))

    repo_root = Path(__file__).resolve().parent.parent
    rc = cli.main([
        "classify", "--kind", "scan", "--ingest-result", str(drop), "--state-dir", str(tmp_path / "state"),
        "--baseline-dir", str(repo_root / "docs" / "partners"),
    ])
    assert rc == 0
    report = list((tmp_path / "state" / "reports").glob("*-scan.md"))[0].read_text()
    assert len(report.encode()) > 323
    assert "## Partner: omnissa" in report
    assert "requested 2/2, completed 0/0" in report
