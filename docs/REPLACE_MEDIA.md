# Replace Media

**Replace Media** is a repair queue for torrents whose trackers repeatedly report
terminal errors such as “unregistered torrent” or “torrent deleted.” It shares
candidate comparison and download handoff code with Upgrade Media, but an
equivalent release is eligible even when it provides no quality or CF upgrade.

## Detection

Every recently observed live client must have exclusively confirmed terminal
tracker endpoints. Endpoint evidence and live observations must be within two
hours. Timeouts, authentication failures, uninspected clients, and archived-only
torrents do not establish source removal. Existing connected peers may still
transfer data; tracker removal is a loss of that discovery source, not proof
that all downloads have become impossible.

The queue groups affected movie releases and show seasons, distinguishes current
media from missing media, and identifies another recently working associated
source when known. Unidentified or unscorable torrents remain visible with a
blocking explanation. Show replacement currently uses season packs and retains
the existing aired-season and coverage guards. Resolve unknown identity/quality
before searching; title/year/edition checks fail closed on mismatches.

## Finding and choosing a replacement

Use **Search** on one row, or **Search replacements** for the visible queue.
Optionally enable **Search for Media Replacements** in Tasks for recurring
searches. Searches never start downloads. Snoozed and ignored issues are excluded
from bulk scheduled searches; the queue can reveal them and return them to review.

Candidates must match the intended title, movie year and edition or show season,
meet the assigned profile's quality/CF minimums, maintain or improve current
quality, and have a CF score at least as high as every replaced release evaluated
under the current profile. Known zero-seeder results are excluded; unknown seeder
counts are shown as unknown. Existing torrent hashes are excluded when visible
in magnets and checked again after fetching a torrent file, before submission.

**Review replacement** displays the current release, candidate score, source,
reported seeders, matched formats, and alternate results. Choose a qBittorrent
client and confirm the download. Eligibility and profile state are revalidated
before submission; another active upgrade/replacement for the same release
blocks a competing submission. Unknown or lower-scoring results are not silently
accepted. If nothing qualifies, the issue stays available for a later search.

## Completion and cleanup

The previous torrent, archive, and media are preserved during downloading and
validation. The replacement must become the validated media association and
have a verified local recovery archive. Show handoff verifies episode coverage.

After validation, choose independently:

- **Retire old torrents** to remove only the old qBittorrent entries, keeping
  downloaded files and torrent archives.
- **Review file cleanup** to preview and confirm filesystem deletion. This is
  blocked while old torrent entries are still present and retains the existing
  writable-root, shared-file, replacement-presence, and signed-preview checks.
- **Keep old media and finish** to close the workflow while retaining old data
  and any old torrent entries.

Replacement history is retained through the candidate and torrent records.
Reusing existing files with a different torrent via recheck is deferred. Google
Drive backup coverage and tracker availability remain separate statuses: a
verified cloud snapshot cannot guarantee that a tracker will keep a release.
