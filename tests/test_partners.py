"""partners.py: loads config/partners.yaml's label allowlist."""

from omnissa_agent.gmail_ingest import EXPECTED_LABEL_NAME
from omnissa_agent.partners import PartnerConfig, load_partner_allowlist, parse_partners_yaml

REAL_CONFIG = (
    "partners:\n"
    "  - id: omnissa\n"
    "    label: \"Archive_/@omnissa.com\"\n"
    "  - id: microsoft\n"
    "    label: \"Archive_/@microsoft.com\"\n"
)


def test_parses_id_label_pairs_in_order():
    result = parse_partners_yaml(REAL_CONFIG)
    assert result == [
        PartnerConfig(id="omnissa", label="Archive_/@omnissa.com"),
        PartnerConfig(id="microsoft", label="Archive_/@microsoft.com"),
    ]


def test_ignores_full_line_comments():
    text = REAL_CONFIG + "  # - id: barracuda\n  #   label: \"Archive_/@barracuda.com\"\n"
    result = parse_partners_yaml(text)
    assert len(result) == 2  # the commented-out entry never appears
    assert all(p.id != "barracuda" for p in result)


def test_ignores_inline_comments_and_blank_lines():
    text = (
        "partners:\n"
        "\n"
        "  - id: omnissa  # primary partner\n"
        "    label: \"Archive_/@omnissa.com\"  # exact match only\n"
        "\n"
    )
    result = parse_partners_yaml(text)
    assert result == [PartnerConfig(id="omnissa", label="Archive_/@omnissa.com")]


def test_unquoted_values_also_parse():
    text = "partners:\n  - id: omnissa\n    label: Archive_/@omnissa.com\n"
    result = parse_partners_yaml(text)
    assert result[0].label == "Archive_/@omnissa.com"


def test_load_partner_allowlist_from_the_real_repo_config():
    result = load_partner_allowlist()
    ids = {p.id for p in result}
    assert ids == {
        "omnissa", "microsoft", "barracuda", "tdsynnex", "arrow", "carahsoft", "planetbids", "zireh",
    }
    # google/zoom are deliberately commented out pending a confirmed
    # exact label name via `cli.py list-labels` -- must not silently
    # appear just because someone asked for them in conversation.
    assert "google" not in ids
    assert "zoom" not in ids


def test_carahsoft_and_zireh_labels_have_no_dot_com_suffix_exactly_as_confirmed():
    """These two really don't follow the "@<domain>.com" pattern the
    others do -- confirmed by the operator via `cli.py list-labels`,
    not a typo to "fix" back to the pattern."""
    result = {p.id: p.label for p in load_partner_allowlist()}
    assert result["carahsoft"] == "Archive_/@carahsoft"
    assert result["zireh"] == "Archive_/@zireh"


def test_load_partner_allowlist_falls_back_to_single_omnissa_when_no_config_file(tmp_path):
    result = load_partner_allowlist(tmp_path / "does-not-exist.yaml")
    assert result == [PartnerConfig(id="omnissa", label=EXPECTED_LABEL_NAME)]


def test_load_partner_allowlist_reads_an_explicit_custom_path(tmp_path):
    p = tmp_path / "custom.yaml"
    p.write_text("partners:\n  - id: acme\n    label: \"Archive_/@acme.com\"\n")
    result = load_partner_allowlist(p)
    assert result == [PartnerConfig(id="acme", label="Archive_/@acme.com")]
