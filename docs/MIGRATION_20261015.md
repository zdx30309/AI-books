# Personal repository migration preparation

Destination: https://github.com/zdx30309/AI-books

This independent copy preserves the available local branch history with credentials removed. Commit hashes differ from the source repository because the history has been sanitized. The original repository, complete original Git bundle, and local work-in-progress backup remain on the owner's computer.

Private environment files, service-account credentials, local assistant settings, proxy configurations, and generated backend output have been excluded from all migrated history. Credential occurrences found elsewhere in source/history have been replaced with `REMOVED_PRIVATE_CREDENTIAL`. Deployment requires separately configured environment secrets; the repository is not a backup of the production database or generated books.

The latest previously uncommitted OpenAI/batch-budget changes were preserved in the migration snapshot. Follow-up fixes passed 466 offline tests (one skipped) before production release `b3dfddc`. Migration itself did not start generation; the owner subsequently authorized continuing the ten-book generation batch within the previously approved shared CNY 100 limit. Real generation completion must be checked separately.

Upload completed after write access was granted: `main` and the five other available local source branches were pushed without overwriting an existing destination branch. The remote `main` commit was independently checked with `git ls-remote`. Original shared-repository history was not force-pushed or deleted. New work continues in the personal repository.
