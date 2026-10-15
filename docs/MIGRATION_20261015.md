# Personal repository migration preparation

Destination: https://github.com/zdx30309/AI-books

This independent copy preserves the available local branch history with credentials removed. Commit hashes differ from the source repository because the history has been sanitized. The original repository, complete original Git bundle, and local work-in-progress backup remain on the owner's computer.

Private environment files, service-account credentials, local assistant settings, proxy configurations, and generated backend output have been excluded from all migrated history. Credential occurrences found elsewhere in source/history have been replaced with `REMOVED_PRIVATE_CREDENTIAL`. Deployment requires separately configured environment secrets; the repository is not a backup of the production database or generated books.

The latest uncommitted OpenAI/batch-budget changes and their tests are included as a work-in-progress snapshot, not as a verified production release. Their targeted tests passed in the source workspace; full end-to-end paid generation has not been performed. Migration does not deploy these changes or start any paid generation.

The public destination requires write access before upload. Local preparation alone does not confirm that GitHub has received the migration.
