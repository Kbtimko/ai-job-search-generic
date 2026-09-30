import json
import json as _json
from pathlib import Path as _Path
from pathlib import Path

from jsonschema import ValidationError

from scripts.store import append_jobs, load_store, save_store, update_jobs, validate_item


def _mkjob(**over):
    base = {"id": "acme-1", "title": "Analyst", "company": "Acme",
            "location_type": "remote", "url": "https://x.com/1",
            "found_date": "2026-06-28", "status": "new"}
    base.update(over)
    return base


def test_update_jobs_applies_patch_by_id():
    existing = [_mkjob(id="a"), _mkjob(id="b")]
    merged, changed = update_jobs(existing, [{"id": "b", "status": "closed",
                                              "posting_checked_date": "2026-07-02"}])
    assert changed == 1
    b = next(j for j in merged if j["id"] == "b")
    assert b["status"] == "closed" and b["posting_checked_date"] == "2026-07-02"
    a = next(j for j in merged if j["id"] == "a")
    assert a["status"] == "new" and "posting_checked_date" not in a


def test_update_jobs_skips_unknown_id():
    existing = [_mkjob(id="a")]
    merged, changed = update_jobs(existing, [{"id": "zzz", "status": "closed"}])
    assert changed == 0
    assert merged[0]["status"] == "new"


def test_update_jobs_does_not_mutate_input():
    existing = [_mkjob(id="a")]
    update_jobs(existing, [{"id": "a", "status": "closed"}])
    assert existing[0]["status"] == "new"  # deep-copied, caller untouched


def test_applications_schema_allows_draft_and_gmail_entry():
    from scripts import store
    schema_path = _Path("schema/applications.schema.json")
    schema = _json.loads(schema_path.read_text())
    stage_enum = schema["items"]["properties"]["stage"]["enum"]
    assert "draft" in stage_enum
    # A draft entry with null applied_date and no job_id must validate.
    store.validate_item(
        {"company": "Acme", "title": "Director, Analytics", "stage": "draft",
         "applied_date": None, "stage_history": [{"stage": "draft", "date": "2026-07-01", "source": "tailor"}]},
        "applications",
    )

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_store_missing_returns_empty(tmp_path: Path):
    assert load_store(tmp_path / "nope.json") == []


def test_load_store_empty_file_returns_empty(tmp_path: Path):
    p = tmp_path / "empty.json"
    p.write_text("   ")
    assert load_store(p) == []


def test_load_store_reads_array(tmp_path: Path):
    p = tmp_path / "jobs.json"
    p.write_text(json.dumps([{"id": "a"}]))
    assert load_store(p) == [{"id": "a"}]


def test_load_store_rejects_non_array(tmp_path: Path):
    p = tmp_path / "obj.json"
    p.write_text(json.dumps({"id": "a"}))
    try:
        load_store(p)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_save_then_load_roundtrip(tmp_path: Path):
    p = tmp_path / "nested" / "jobs.json"
    items = [{"id": "a"}, {"id": "b"}]
    save_store(p, items)
    assert load_store(p) == items


def test_validate_item_accepts_valid_job():
    job = json.loads((FIXTURES / "valid_job.json").read_text())
    validate_item(job, "jobs")  # should not raise


def test_validate_item_rejects_invalid_job():
    job = json.loads((FIXTURES / "invalid_job.json").read_text())
    try:
        validate_item(job, "jobs")
        assert False, "expected ValidationError"
    except ValidationError:
        pass


def test_validate_item_rejects_bad_date_format():
    job = {
        "id": "x", "title": "Senior Data Analyst", "company": "Acme",
        "location_type": "remote", "url": "https://x.com/1",
        "found_date": "not-a-date", "status": "new",
    }
    try:
        validate_item(job, "jobs")
        assert False, "expected ValidationError for bad date"
    except ValidationError:
        pass


def test_validate_item_rejects_bad_status_enum():
    job = {"id": "x", "title": "T", "company": "C", "location_type": "remote",
           "url": "https://x.com/1", "found_date": "2026-06-28", "status": "nope"}
    try:
        validate_item(job, "jobs"); assert False
    except ValidationError:
        pass


def test_validate_item_rejects_missing_required():
    job = {"id": "x", "company": "C", "location_type": "remote",
           "url": "https://x.com/1", "found_date": "2026-06-28", "status": "new"}  # no title
    try:
        validate_item(job, "jobs"); assert False
    except ValidationError:
        pass


def test_validate_item_rejects_score_out_of_range():
    job = {"id": "x", "title": "T", "company": "C", "location_type": "remote",
           "url": "https://x.com/1", "found_date": "2026-06-28", "status": "new", "score": 999}
    try:
        validate_item(job, "jobs"); assert False
    except ValidationError:
        pass


def _job(id_, url, company="Acme", title="Senior Data Analyst"):
    return {"id": id_, "url": url, "company": company, "title": title}


def test_append_jobs_adds_new():
    existing = [_job("a", "https://x.com/1")]
    incoming = [_job("b", "https://x.com/2", company="Beta")]
    merged, added = append_jobs(existing, incoming)
    assert added == 1
    assert len(merged) == 2


def test_append_jobs_dedupes_by_url():
    existing = [_job("a", "https://x.com/1")]
    incoming = [_job("b", "https://x.com/1/")]  # trailing slash, same url
    merged, added = append_jobs(existing, incoming)
    assert added == 0
    assert len(merged) == 1


def test_append_jobs_dedupes_by_company_title():
    existing = [_job("a", "https://x.com/1")]
    incoming = [_job("b", "https://y.com/2")]  # different url, same company+title
    merged, added = append_jobs(existing, incoming)
    assert added == 0


def test_append_jobs_dedupes_within_incoming():
    existing = []
    incoming = [_job("a", "https://x.com/1"), _job("b", "https://x.com/1")]
    merged, added = append_jobs(existing, incoming)
    assert added == 1


def test_append_jobs_url_only_distinct_not_collapsed():
    # Jobs missing company/title must dedupe by URL only, not collapse on empty composite key
    existing = []
    incoming = [{"url": "https://a.com/1"}, {"url": "https://b.com/2"}]
    merged, added = append_jobs(existing, incoming)
    assert added == 2


from scripts.store import application_key, is_forward_transition, upsert_application


def test_application_key_prefers_job_id_else_company_title():
    assert application_key({"job_id": "acme-dir-2026-06"}) == "acme-dir-2026-06"
    assert application_key({"company": "Acme", "title": "Director"}) == "acme::director"


def test_is_forward_transition_rules():
    assert is_forward_transition("applied", "interview") is True
    assert is_forward_transition("interview", "applied") is False        # backward
    assert is_forward_transition("applied", "applied") is False          # equal
    assert is_forward_transition("screening", "rejected") is True        # terminal from any active
    assert is_forward_transition("offer", "withdrawn") is True           # terminal from offer
    assert is_forward_transition("rejected", "interview") is False       # nothing leaves terminal


def test_upsert_advances_stage_and_appends_history():
    existing = [{"company": "Acme", "title": "Director", "stage": "applied",
                 "applied_date": "2026-06-20", "stage_history": [
                     {"stage": "applied", "date": "2026-06-20", "source": "manual"}]}]
    inc = {"company": "Acme", "title": "Director", "stage": "interview",
           "source": "gmail", "evidence": "Interview invitation"}
    merged, changed = upsert_application(existing, inc, "2026-07-01")
    assert changed is True
    assert merged[0]["stage"] == "interview"
    assert merged[0]["stage_history"][-1] == {
        "stage": "interview", "date": "2026-07-01", "source": "gmail", "evidence": "Interview invitation"}


def test_upsert_ignores_backward_transition():
    existing = [{"company": "Acme", "title": "Director", "stage": "interview",
                 "applied_date": "2026-06-20", "stage_history": []}]
    inc = {"company": "Acme", "title": "Director", "stage": "applied", "source": "gmail"}
    merged, changed = upsert_application(existing, inc, "2026-07-01")
    assert changed is False
    assert merged[0]["stage"] == "interview"
    assert merged[0]["stage_history"] == []  # no-op path must not append history


def test_upsert_creates_new_applied_entry_with_date():
    merged, changed = upsert_application([], {"company": "Beta", "title": "Analytics Lead",
                                              "stage": "applied", "source": "manual"}, "2026-07-01")
    assert changed is True
    assert merged[0]["applied_date"] == "2026-07-01"
    assert merged[0]["stage_history"] == [{"stage": "applied", "date": "2026-07-01", "source": "manual"}]


def test_upsert_creates_draft_with_null_applied_date():
    merged, _ = upsert_application([], {"company": "Beta", "title": "X", "stage": "draft",
                                        "source": "tailor", "artifacts_path": "output/beta-x"}, "2026-07-01")
    assert merged[0]["applied_date"] is None
    assert merged[0]["artifacts_path"] == "output/beta-x"


def test_upsert_does_not_mutate_input_existing():
    existing = [{"company": "Acme", "title": "Director", "stage": "applied",
                 "applied_date": "2026-06-20",
                 "stage_history": [{"stage": "applied", "date": "2026-06-20", "source": "manual"}]}]
    inc = {"company": "Acme", "title": "Director", "stage": "interview", "source": "gmail"}
    merged, _ = upsert_application(existing, inc, "2026-07-01")
    # caller's original must be untouched
    assert existing[0]["stage"] == "applied"
    assert len(existing[0]["stage_history"]) == 1
    assert merged[0]["stage"] == "interview"
    assert len(merged[0]["stage_history"]) == 2


def test_load_store_dispatches_to_github_backend(monkeypatch, tmp_path):
    import scripts.store as store
    calls = {}

    fake = type("M", (), {})()

    def _fake_load(path):
        calls["load"] = path
        return [{"ok": 1}]

    def _fake_save(path, items):
        calls["save"] = (path, items)

    fake.load = _fake_load
    fake.save = _fake_save
    monkeypatch.setitem(__import__("sys").modules, "scripts.github_store", fake)
    monkeypatch.setenv("STORE_BACKEND", "github")

    p = tmp_path / "applications.json"
    assert store.load_store(p) == [{"ok": 1}]
    store.save_store(p, [{"a": 2}])
    assert calls["load"] == p
    assert calls["save"] == (p, [{"a": 2}])


def test_local_backend_is_default(monkeypatch, tmp_path):
    import scripts.store as store
    monkeypatch.delenv("STORE_BACKEND", raising=False)
    p = tmp_path / "x.json"
    store.save_store(p, [{"a": 1}])
    assert store.load_store(p) == [{"a": 1}]
