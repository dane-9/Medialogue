# Custom Libraries — plan

Plex-style libraries: a library is a name, a kind (movies | shows) and the media folders it contains.
Each library gets its own sidebar entry. Example use: Movies, Shows, Cartoon Movies, Cartoon Shows,
each with its own folders and qBittorrent category, on the existing per-kind qBit clients.

## Decisions

- **Library** = `name, kind, sort_order`. Kind is fixed once the library has folders.
- **A media folder belongs to at most one library.** A folder in no library is treated as if it does
  not exist: not scanned, not watched, not health-checked, not offered for downloads, torrents in it
  are not matched.
- **The qBit category lives on the media folder** (its save path describes that folder). Download
  clients keep their per-kind scope; nothing changes there.
- **Every title belongs to one library** (`library_id` required), set once when the title is created:
  the library of the folder it was first found in, or of the folder picked in step 1 of the download
  wizard. Scans never move titles between libraries; moving a folder updates its titles explicitly.
  Files later found in another library's folder are only logged as a warning in v1.
- **TMDB ids stay globally unique.** A title cannot be in two libraries; searching for a title that is
  in another library shows it as already in the library.
- **Torrent → library** comes from its path (path → folder → library). Category is only used when grabbing.
- **Deleting a library is refused** while it has folders or titles.
- **Unassigning a folder is refused** while it holds titles ("Move it by checking it in another
  library, or remove the folder"). A folder leaves a library by being checked in another one.
- **Media folders keep `media_type`.** A folder can only join a library of the same kind.
- **Moving a folder to another library** (same kind) moves its titles with it in the same save; the
  dialog says so before saving.
- **Upgrades** use the title's current folder and category; moving a title between libraries is out
  of scope.
- **Migration** creates "Movies" and "Shows" and puts every existing folder and title in the one
  matching its media type. Fresh installs get both, empty. Both can be renamed or deleted like any other.

## Data model (migration 0008)

- New `libraries`: `id, name (unique), kind, sort_order, created_at, updated_at`.
- `storage_roots`: add `library_id` (nullable FK, `ON DELETE RESTRICT`), add `category` (nullable);
  `media_type` stays.
- `movies`, `shows`: add `library_id` (FK, NOT NULL after backfill, `RESTRICT`).
- Backfill: create Movies/Shows, assign roots by `media_type`, titles by kind. A folder's `category`
  starts empty, meaning the client's default category, which is what grabs use today.

## Backend

1. **Libraries API**: list / create / rename / reorder / delete (`api/libraries.py`), and
   `PUT /libraries/{id}/folders` to set membership (checking moves, refusals as above).
2. **Storage API**: folder create/update adds `category`; responses carry `library_id`, `library_name`.
3. **Unassigned = invisible.** Add `StorageRoot.library_id IS NOT NULL` (or an equivalent check) to
   every place that selects or acts on roots:
   `qbittorrent.py:500`, `torrent_archive.py:806`, `torrent_reconciliation.py:302/393`,
   `library_scan.py:146/163`, `media_watcher.py:88/126`, `reconciliation.py:827/843`,
   `scheduled_media.py:72`, `download_deletion.py:315`, `health_sweep.py:74`, `api/storage.py:416`.
   Scan of an unassigned root is refused.
4. **Title library assignment**: a title created by a scan gets its folder's library; an existing title
   whose files appear in another library's folder stays put and a warning is logged.
5. **Grabs**: unattached movie/show grabs take `storage_root_id`; the new title gets that folder's
   library; the category defaults to the folder's category (the per-grab override stays).
   Upgrades/replacements default to the title's folder category.
6. **Library filter** (`library_id`) on movies/shows lists, New Media, Upgrade, Replace, Problems,
   Downloads.

## Frontend

1. **Sidebar** built from `/libraries` (fixed icon by kind), drag to reorder. Hovering an entry shows ⋯:
   Scan library / Edit library / Delete library.
2. **Right-click the titlebar → Add library.**
3. **Library dialog**: General (name, kind), Folders (checklist of every folder of the library's kind, each showing its current library; moving shows a warning),
   Advanced (empty in v1).
4. **Routes**: `/library/:libraryId` renders the existing Movies or Shows page filtered to that library;
   `/movies/:id`, `/shows/:id` stay; `/movies` and `/shows` redirect to the first library of that kind.
5. **Settings → Media → Media Folders**: form keeps Media type, adds Category (picked from that kind's
   client categories); rows show
   `Name (Library)` or `(No library)`; Scan now disabled with a tooltip on unassigned folders.
6. **Download wizards** (`MovieDownloadWizard`, `ShowDownloadWizard`): new step 1 "Media folder"
   listing folders of the kind grouped by library, skipped when only one exists; Setup becomes step 2
   with the category defaulting from the folder.
7. **Work queues**: library filter; media folder cards read `Movies (Movies)`, the library in smaller text.

## Phases

1. Migration, models, Libraries API, storage API changes, unassigned-is-invisible (backend 1–3) + tests.
2. Title assignment on creation and on folder moves (backend 4) + tests.
3. Sidebar, titlebar menu, ⋯ menu, library dialog, routes (frontend 1–4).
4. Media folder form and rows (frontend 5).
5. Wizard step 1 and grab/upgrade category defaults (backend 5, frontend 6).
6. Library filters and folder card labels (backend 6, frontend 7).

## Later

- Map a library to a Plex section by overlapping folders.
- Per-library default quality profile (Advanced tab).
- "Title split across libraries" Problem, if the logged warning ever shows up.
- Moving a title between libraries (moves files and the torrent's location).
