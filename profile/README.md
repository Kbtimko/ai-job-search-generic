# profile/

`master.json` is your structured résumé: the single source the scan scores against and the
tailoring skill writes from. It is created by `/setup profile` from your résumé file.

It is tracked in your **private** repo, because the daily scan reads it in CI. Keep this
repo private.

`master.example.json` shows the expected shape, including the `scoring_context` block that
tells the scorer what makes you different. Everything else in `profile/` stays untracked.
