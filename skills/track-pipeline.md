# Skill: track-pipeline

**Purpose:** Maintain `data/applications.json` — the record of every application
and its stage — and review the deterministic Gmail status scan. Advance stages,
confirm ambiguous detections, and log manual applications. Contacts/calendar are
out of scope (later phase).

**Inputs:**
- `data/applications.json` — the pipeline (source of truth).
- `data/pipeline-state.json` — last scan time + processed message ids.
- `data/jobs.json` — to resolve "I applied to X" to a known job.

**Tools used:** the store CLI (`scripts.store upsert-applications`), the pipeline
scanner (`scripts/pipeline_scan.py`), Gmail (read-only, via the scanner).

**Safety:** Never applies to a job or sends any email except the pipeline digest.
Only high-confidence, forward stage changes are auto-applied by the daily scan;
this skill is where you confirm the rest.

## Procedure

1. **Show the pipeline.** Read `data/applications.json`. Present entries grouped
   by stage (draft → applied → screening → interview → offer → rejected/withdrawn),
   most-recent movement first. Note `next_action`/`due_date` and any stalled
   entries (no movement in 14+ days).

2. **Run / read the scan (optional).** If asked to check email now, run
   `PYTHONPATH=. python scripts/pipeline_scan.py` (requires `GMAIL_APP_PASSWORD`
   and IMAP enabled in Gmail). It auto-applies high-confidence changes and prints
   how many are ambiguous. Otherwise, review the latest emailed digest.

3. **Confirm ambiguous detections.** For each "Confirm?" item the user approves,
   write a slim incoming record and upsert it:
   ```
   echo '[{"company":"Beta Corp","title":"Lead","stage":"interview","source":"manual","evidence":"confirmed from digest"}]' > /tmp/inc.json
   python -m scripts.store upsert-applications --data data/applications.json --incoming /tmp/inc.json
   ```

4. **Log a manual application ("I applied to X").** Resolve X against
   `data/jobs.json` (match company/title; capture `job_id` if found). Upsert an
   `applied` entry:
   ```
   echo '[{"job_id":"<id-or-omit>","company":"Acme","title":"Director","stage":"applied","source":"manual"}]' > /tmp/inc.json
   python -m scripts.store upsert-applications --data data/applications.json --incoming /tmp/inc.json
   ```

5. **Manual stage edits / withdraw.** Same CLI; the stage must be a forward move
   (or a terminal `rejected`/`withdrawn`). Backward edits are refused by the store.

## Notes
- Applications are written ONLY through `scripts.store upsert-applications`
  (schema-validated, forward-only). Never hand-edit `data/applications.json`.
- The daily GitHub Action (`.github/workflows/pipeline-scan.yml`) runs the same
  scanner unattended at **$0** (deterministic, no LLM).
