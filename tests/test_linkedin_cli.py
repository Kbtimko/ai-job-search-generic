from pathlib import Path

from linkedin.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"
JOBS = str(FIXTURES / "jobs.json")


def test_manifest_subcommand_lists_found_files(capsys, tmp_path):
    code = main(["manifest", "--archive", str(FIXTURES), "--output", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Connections.csv" in out
    assert "Saved Jobs_1.csv" in out  # reported as missing, not silently dropped


def test_warm_intros_subcommand_writes_files(capsys, tmp_path):
    code = main(["warm-intros", "--archive", str(FIXTURES), "--output", str(tmp_path),
                 "--jobs", JOBS])
    assert code == 0
    assert (tmp_path / "warm_intros_active.csv").exists()
    out = capsys.readouterr().out
    assert "coverage" in out.lower()
    # the uncontacted view is most of the network -- it must not be buried
    assert "warm_intros_uncontacted.csv" in out


def test_inbound_subcommand_writes_files(tmp_path):
    code = main(["inbound", "--archive", str(FIXTURES), "--output", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "inbound_reply_rate.csv").exists()


def test_funnel_subcommand_writes_files(tmp_path):
    code = main(["funnel", "--archive", str(FIXTURES), "--output", str(tmp_path),
                 "--jobs", JOBS])
    assert code == 0
    assert (tmp_path / "applications_enriched.csv").exists()


def test_missing_archive_directory_exits_nonzero(capsys, tmp_path):
    code = main(["manifest", "--archive", str(tmp_path / "nope"), "--output", str(tmp_path)])
    assert code == 1
    assert "not found" in capsys.readouterr().out.lower()


def test_no_subcommand_prints_usage_and_exits_nonzero(capsys):
    assert main([]) == 2
