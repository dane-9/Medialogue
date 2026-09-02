# ELI5: GitHub → GHCR → TrueNAS SCALE / Dockge

CI runs automatically for pull requests and pushes to `main`. Publishing a
container image remains a deliberate manual action.

Uploading or pushing code to GitHub runs the checks, but does **not** publish a
Docker image.

You decide when an image is published and deployed.

```text
Upload or push code to GitHub
        ↓
CI checks pull requests and the main branch
        ↓
WHEN YOU WANT TO PUBLISH:
manually click "Run workflow" for Publish GHCR Image
        ↓
GHCR stores the image
        ↓
YOU manually tell Dockge to pull/redeploy it
```

## 1. Put the source on GitHub

Create a GitHub repository named `medialogue` and upload the contents of this Medialogue folder.

**Important if you are replacing an older upload through GitHub's web interface:** uploading new files does not delete obsolete files already in the repository. Remove stale files explicitly when instructed. This package is hardened so the old `backend/alembic/__init__.py` file cannot be installed as the real Alembic package, but keeping the repository clean is still recommended.

Do not upload your real `.env` file. The repository `.gitignore` explicitly ignores `.env` and other `.env.*` files except the supplied examples.

After the upload, CI runs on pull requests and pushes to `main`. The publish
workflow remains manual.

## 2. Review the automatic CI checks

The complete test/build checks run automatically for a pull request or a push
to `main`. You can also run them on demand:

1. Open the repository.
2. Click **Actions**.
3. Click **CI**.
4. Click **Run workflow**.
5. Click the green **Run workflow** button.

The manual option is useful when you want to rerun the checks without a new
commit. It does not publish an image.

It does not publish an image.

## 3. Manually publish a GHCR image

When YOU decide you want a Docker image:

1. Open the repository.
2. Click **Actions**.
3. Click **Publish GHCR Image (manual)**.
4. Click **Run workflow**.
5. Leave the tag as:

   ```text
   latest
   ```

   unless you deliberately want a different tag.

6. Click the green **Run workflow** button.

There is no typed confirmation phrase; choosing the publish workflow and clicking **Run workflow** is the confirmation.

Only then does GitHub build and push:

```text
ghcr.io/YOUR_GITHUB_USERNAME/medialogue:latest
```

It also publishes a `sha-...` tag for that exact commit.

CI has pull-request, `main` push, and manual triggers. The publish workflow
has a manual trigger only.

## 4. Make the GHCR package available to Dockge

The first GHCR package is normally private.

For the simplest home-server deployment, make the **container package** public:

1. GitHub profile → **Packages**.
2. Open `medialogue`.
3. **Package settings**.
4. **Change visibility → Public**.

Your source repository can remain private if you want. Only the container package needs to be public for anonymous pulls.

## 5. Create persistent locations on TrueNAS

Create locations such as:

```text
/mnt/YOUR_POOL/appdata/medialogue/config
/mnt/YOUR_POOL/appdata/medialogue/torrent-archive
```

Keep using your existing Movie/Show datasets. Do not copy your media.

For example:

```text
/mnt/YOUR_POOL/media/movies
/mnt/YOUR_POOL/media/shows
```

## 6. Create a stack in Dockge

In Dockge:

1. Create a stack named `medialogue`.
2. Paste the contents of `compose.truenas-dockge.yml` into the Compose editor.
3. **Do not put passwords into the Compose text.**
4. Open Dockge's `.env` editor for this stack.
5. Enter your real local values there.

Start from `.env.truenas-dockge.example`, but enter the real values only in Dockge:

```env
GITHUB_USERNAME=your-github-username
MEDIALOGUE_IMAGE_TAG=latest
MEDIALOGUE_PORT=8000

PUID=568
PGID=568
TZ=Europe/Stockholm

# Optional. If omitted, Medialogue persists a generated key at
# /config/application-secret. Keep that file with the config dataset.
# MEDIALOGUE_SECRET_KEY=<generated-random-value>

CONFIG_HOST_PATH=/mnt/tank/appdata/medialogue/config
TORRENT_ARCHIVE_HOST_PATH=/mnt/tank/appdata/medialogue/torrent-archive
MOVIES_HOST_PATH=/mnt/tank/media/movies
SHOWS_HOST_PATH=/mnt/tank/media/shows
```

The values in Dockge stay on your TrueNAS stack. They are not committed to GitHub.

Medialogue writes its SQLite database and integration configuration to `CONFIG_HOST_PATH`: `medialogue.json` contains non-secret Plex/TMDB/qBittorrent/indexer settings and `secrets.enc` contains the corresponding encrypted credentials. Keep the same `MEDIALOGUE_SECRET_KEY` whenever you retain `secrets.enc`. Use a local TrueNAS dataset for this path, not an SMB/NFS share.

This build intentionally starts with a fresh SQLite database and does not import an older PostgreSQL database. Configure integrations from the UI, or retain file-backed `/config` files from a later compatible build.

If you choose an explicit override, generate a safe random value, for example:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use the generated value for `MEDIALOGUE_SECRET_KEY`. Otherwise, back up
`/config/application-secret` together with the config dataset.

## 7. Start Medialogue manually

In Dockge, click **Save**, then **Start / Up**.

Dockge pulls:

```text
ghcr.io/YOUR_GITHUB_USERNAME/medialogue:latest
```

Open:

```text
http://YOUR_TRUENAS_IP:8000
```

Initial setup:

```sh
docker compose exec medialogue cat /config/initial-setup-token
```

Paste the one-time token into the First Run screen and choose an administrator
password of at least 12 characters. The token is deleted after setup.

The supplied deployment defaults to the non-root TrueNAS identity
(`PUID=568`, `PGID=568`). UID 0 is rejected unless
`MEDIALOGUE_ALLOW_ROOT=true` is explicitly set for a compatibility
deployment.

## 8. Storage paths inside Medialogue

If TrueNAS has:

```text
/mnt/tank/media/movies
```

and Compose maps it to:

```text
/media/movies
```

then Medialogue's Movie Storage Root is:

```text
/media/movies
```

The TrueNAS `/mnt/...` path belongs only on the Dockge/Compose side.

## 9. First-run order

Recommended:

```text
1. Complete administrator first-run setup
2. Configure TMDB
3. Configure Plex
4. Configure Movie qBittorrent
5. Configure Show qBittorrent instance(s)
6. Add /media/movies as Movie Storage Root
7. Add /media/shows as Show Storage Root
8. Configure indexers
9. Test integrations
10. Initialize each storage root by pressing Scan once when ready
11. Start first scan manually
```

The supplied media mounts are read-only for the first deployment. The normal
TrueNAS example uses non-root `PUID=568` and `PGID=568`; UID 0 is rejected
unless `MEDIALOGUE_ALLOW_ROOT=true` is deliberately configured.

## 10. Updating later

When you want a new container image:

```text
1. Upload/push your new code and review the automatic CI result
2. Confirm the persistent `/config` dataset is retained
3. Run Publish GHCR Image (manual)
4. In Dockge, manually pull/redeploy Medialogue
```

You remain in control of every step.

## Container entrypoint portability

The Dockerfile explicitly sets the Medialogue entrypoint to executable during the image build. This does not depend on Unix file-mode metadata surviving ZIP extraction, GitHub web uploads, or a Windows checkout. Shell scripts are also forced to LF line endings through `.gitattributes`.
