# Backup, recovery, and replacement implementation

Agreed scope: automatic local torrent capture; per-client recovery evidence;
honest local/remote/source statuses; encrypted scheduled Google Drive snapshots
with retention; archive rebuilding and guided recovery; Replace Media with
equivalent-or-better quality and custom-format matching. Reuse of existing data
with a different torrent is deferred.

## Delivery sequence

- [x] Archive foundations: preserve client-specific recovery snapshots, select
  the correct snapshot on restore, expose verification and capture evidence,
  and rebuild records from archive manifests.
- [x] Backup service: staged snapshots, portable encryption, destination setup,
  Google OAuth and resumable uploads, verified uploads, schedule and retention.
- [x] Recovery experience: inspect backups, guide offline application restore,
  and re-import torrents with explicit destination review.
- [x] Replace Media: confirmed source failures, grouped affected releases,
  manual search/selection, equal-or-better eligibility, validated handoff and
  separate cleanup.
- [x] Validation: targeted backend tests, frontend tests/build, generated API
  contract checks, recovery documentation and a fresh-database recovery test.

## Invariants

Local archiving, remote backup coverage, and tracker availability are separate
facts. Preserve old archives and media until replacement validation succeeds.
Never infer source failure from authentication errors, timeouts, or stale
archive evidence. Encrypt credentials before remote upload. Do not discard the
last successful backup after a failed run. Google account connection requires
deployment OAuth credentials and authorization by the account owner.

## Progress

Implemented using the existing tracker classification, recovery export,
bulk re-import, and upgrade workflows. See [Backup and Restore](BACKUP_RESTORE.md)
and [Replace Media](REPLACE_MEDIA.md) for operation and limitations.

Validation: full backend run returned 620 passed, one platform skip, and one
outdated scheduler assertion. After updating that assertion for the two new
tasks, all 31 tests in the scheduler and backup-rework modules passed. All 296
frontend tests passed; the final production build and generated API contract
check passed. Drive transport was simulated; an actual account upload has not
been exercised. Browser visual inspection was blocked by the browser runtime
bootstrap. Existing-data reuse with a different torrent remains deferred.
