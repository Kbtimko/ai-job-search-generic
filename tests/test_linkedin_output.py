import csv
import inspect

from linkedin.output import write_csv


def test_caveat_is_required_not_defaulted():
    """The one guard that stops FIX-2-style drift recurring. Giving ``caveat`` a
    default would let a new output file ship with a bare header again, which is
    exactly how warm_intros wrote five uncaveated files."""
    caveat = inspect.signature(write_csv).parameters["caveat"]
    assert caveat.default is inspect.Parameter.empty


def test_caveat_precedes_the_header_and_rows_survive(tmp_path):
    path = tmp_path / "nested" / "out.csv"
    write_csv(path, ["a", "b"], [{"a": "1", "b": "2"}], "# careful\n")
    lines = path.read_text().splitlines()
    assert lines[0] == "# careful"
    assert lines[1] == "a,b"
    rows = list(csv.DictReader(line for line in lines if not line.startswith("#")))
    assert rows == [{"a": "1", "b": "2"}]


def test_extra_columns_on_a_row_are_ignored_not_fatal(tmp_path):
    """Callers pass whole tie/stat dicts and name the subset they want written."""
    path = tmp_path / "out.csv"
    write_csv(path, ["a"], [{"a": "1", "unwanted": "x"}], "# c\n")
    assert "unwanted" not in path.read_text()
