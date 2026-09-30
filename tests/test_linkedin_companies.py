from linkedin.companies import (
    MATCH_CONFIG,
    build_match_report,
    load_aliases,
    match_company,
    normalize,
)

TARGETS = ["Northwind Traders", "Contoso", "Globex Corporation", "Initech"]


def test_lowercases_and_collapses_whitespace():
    assert normalize("  Northwind   Traders ") == "northwind traders"


def test_strips_legal_suffixes():
    assert normalize("Contoso, Inc.") == "contoso"
    assert normalize("Fabrikam LLC") == "fabrikam"
    assert normalize("Globex Corp") == "globex"
    assert normalize("Initech Ltd.") == "initech"


def test_strips_leading_the():
    assert normalize("The Trade Desk") == "trade desk"


def test_strips_trailing_parenthetical():
    assert normalize("Acme (formerly Ajax)") == "acme"


def test_strips_punctuation():
    assert normalize("Ben & Jerry's") == "ben jerry s"


def test_blank_input_returns_empty_string():
    assert normalize("") == ""
    assert normalize(None) == ""


def test_does_not_strip_suffix_inside_a_word():
    # "Incorporated Systems" must not lose its "inc".
    assert normalize("Incorporated Systems") == "incorporated systems"


def test_exact_match_after_normalization_auto_accepts():
    result = match_company("Contoso, Inc.", TARGETS)
    assert result.matched == "Contoso"
    assert result.band == "auto"
    assert result.score >= MATCH_CONFIG["auto_accept"]


def test_close_variant_auto_accepts():
    result = match_company("Globex Corp", TARGETS)
    assert result.matched == "Globex Corporation"
    assert result.band == "auto"


def test_unrelated_name_does_not_match():
    result = match_company("Umbrella Biotech", TARGETS)
    assert result.matched is None
    assert result.band == "none"


def test_borderline_match_goes_to_review_not_auto_accept():
    result = match_company("Northwind Trading", TARGETS)
    assert result.band == "review"
    assert result.matched is None, "review-band matches must not be auto-accepted"
    assert result.candidate == "Northwind Traders"


def test_alias_map_forces_a_match():
    result = match_company("NWT", TARGETS, aliases={"nwt": "Northwind Traders"})
    assert result.matched == "Northwind Traders"
    assert result.band == "alias"
    assert result.score == 100


def test_blank_input_returns_no_match():
    assert match_company("", TARGETS).band == "none"


def test_match_report_separates_precision_from_coverage():
    report = build_match_report(
        sources=["Contoso, Inc.", "Globex Corp", "Northwind Trading", "Umbrella Biotech"],
        targets=TARGETS,
    )
    assert report["attempted"] == 4
    assert report["auto"] == 2
    assert report["review"] == 1
    assert report["none"] == 1
    # Precision is measured over matches that landed in a band, not over targets.
    assert report["precision"] == 2 / 3
    # Coverage is a separate statistic and never gates the run.
    assert report["coverage"] == 2 / len(TARGETS)


def test_match_report_precision_is_zero_when_nothing_matched():
    report = build_match_report(sources=["Umbrella Biotech"], targets=TARGETS)
    assert report["precision"] == 0.0


def test_single_token_subset_is_demoted_to_review():
    """token_set_ratio returns 100 for a subset, so "Better" scores 100 against
    "Better Sleep". Single-token company names are common English words; accepting
    that silently attaches the wrong person to the wrong job, and 100 never reaches
    the review band on its own."""
    result = match_company("Better", ["Better Sleep"])
    assert result.band == "review"
    assert result.matched is None
    assert result.candidate == "Better Sleep"


def test_single_token_subset_demotion_applies_to_real_data_case():
    result = match_company("NerdWallet", ["NerdWallet Small Business"])
    assert result.band == "review"
    assert result.matched is None
    assert result.candidate == "NerdWallet Small Business"


def test_demotion_does_not_touch_exact_matches_after_normalization():
    # Equal token sets are not a subset case, single-token or not.
    assert match_company("Contoso, Inc.", ["Contoso"]).band == "auto"
    assert match_company("Globex Corp", ["Globex Corporation"]).band == "auto"


def test_demotion_does_not_touch_genuine_multi_token_near_matches():
    # Scores 96.97: a real near-match, both sides two tokens.
    result = match_company("Northwind Trader", ["Northwind Traders"])
    assert result.band == "auto"
    assert result.matched == "Northwind Traders"


def test_multi_token_subset_still_auto_accepts():
    # "omnicom media" is a subset of "omnicom media group", but two shared tokens
    # are specific enough to trust; only the single-token case is ambiguous.
    result = match_company("Omnicom Media", ["Omnicom Media Group"])
    assert result.band == "auto"
    assert result.matched == "Omnicom Media Group"


def test_single_token_demotion_does_not_apply_to_aliases():
    """An alias entry IS the human confirmation the review band exists to collect.
    Demoting it would make the alias file unable to express its main use case."""
    result = match_company("Better", ["Better Sleep"], aliases={"better": "Better Sleep"})
    assert result.band == "alias"
    assert result.matched == "Better Sleep"


def test_coverage_ignores_matches_that_are_not_in_the_target_list():
    """An alias may point anywhere; only targets can be covered."""
    report = build_match_report(["Zzz Corp"], ["Contoso"], aliases={"zzz": "Not A Target"})
    assert report["alias"] == 1
    assert report["coverage"] == 0.0


def test_coverage_denominator_deduplicates_targets():
    report = build_match_report(["Contoso"], ["Contoso", "Contoso"])
    assert report["coverage"] == 1.0


def test_load_aliases_normalizes_hand_written_keys(tmp_path):
    """aliases.json exists to be hand-edited. A key written the natural way --
    with legal suffix and punctuation -- must not silently never match."""
    path = tmp_path / "aliases.json"
    path.write_text(
        '{"aliases": {"Northwind Traders Inc.": "Northwind Traders"}}', encoding="utf-8"
    )
    aliases = load_aliases(path)
    assert aliases == {"northwind traders": "Northwind Traders"}
    result = match_company("Northwind Traders Inc.", TARGETS, aliases=aliases)
    assert result.band == "alias"
    assert result.matched == "Northwind Traders"


def test_load_aliases_returns_empty_for_a_missing_file(tmp_path):
    assert load_aliases(tmp_path / "nope.json") == {}
