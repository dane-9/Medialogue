# ELI5: Manual GitHub → GHCR → TrueNAS SCALE / Dockge

Nothing in this package publishes automatically.

Uploading or pushing code to GitHub does **not** run CI and does **not** publish a Docker image.

You decide when each action happens.

```text
Upload code to GitHub
        ↓
Nothing happens automatically
        ↓
OPTIONAL: manually click "Run workflow" for CI
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

After the upload, **nothing runs automatically**.

## 2. Optional: manually run the tests

If you want GitHub to run the complete test/build checks:

1. Open the repository.
2. Click **Actions**.
3. Click **CI (manual)**.
4. Click **Run workflow**.
5. Click the green **Run workflow** button.

This runs only because you clicked it.

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

There are **no push, pull-request, tag, or schedule triggers** for either workflow.

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

POSTGRES_PASSWORD=your-real-random-password
MEDIALOGUE_SECRET_KEY=your-real-random-secret

CONFIG_HOST_PATH=/mnt/tank/appdata/medialogue/config
TORRENT_ARCHIVE_HOST_PATH=/mnt/tank/appdata/medialogue/torrent-archive
MOVIES_HOST_PATH=/mnt/tank/media/movies
SHOWS_HOST_PATH=/mnt/tank/media/shows
```

The values in Dockge stay on your TrueNAS stack. They are not committed to GitHub.

Generate safe random values, for example:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use the first for `POSTGRES_PASSWORD` and the second for `MEDIALOGUE_SECRET_KEY`.

## 7. Start Medialogue manually

In Dockge, click **Save**, then **Start / Up**.

Dockge pulls:

```text
ghcr.io/YOUR_GITHUB_USERNAME/medialogue:latest
postgres:16-alpine
```

Open:

```text
http://YOUR_TRUENAS_IP:8000
```

Initial login:

```text
username: admin
password: adminadmin
```

Change the password during first-run setup.

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
1. Change admin password
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

The supplied media mounts are read-only for the first deployment.

## 10. Updating later

Nothing happens merely because you update GitHub.

When you want a new container image:

```text
1. Upload/push your new code
2. Optionally run CI (manual)
3. Run Publish GHCR Image (manual)
4. In Dockge, manually pull/redeploy Medialogue
```

You remain in control of every step.

## Container entrypoint portability

The Dockerfile explicitly sets the Medialogue entrypoint to executable during the image build. This does not depend on Unix file-mode metadata surviving ZIP extraction, GitHub web uploads, or a Windows checkout. Shell scripts are also forced to LF line endings through `.gitattributes`.
