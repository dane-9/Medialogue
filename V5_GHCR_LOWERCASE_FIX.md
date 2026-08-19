# v5 GHCR Lowercase Image Fix

GitHub preserves the repository spelling in `github.repository`. For a repository named `Medialogue`, that value can be `dane-9/Medialogue`. Docker/OCI image repository names must be lowercase.

The manual publish workflow now computes the image name with Bash lowercasing before build, validation, and push:

```text
ghcr.io/dane-9/medialogue:latest
```

The workflow remains fully manual. Uploading or pushing code does not publish an image.

Run:

1. **Actions -> CI (manual) -> Run workflow**
2. **Actions -> Publish GHCR Image (manual) -> Run workflow**
3. Enter `PUBLISH`

Then in Dockge pull/recreate the `latest` image.
