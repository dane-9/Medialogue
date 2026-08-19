# Prowlarr / Torznab Setup

Medialogue does not import the complete Prowlarr configuration.

Add each indexer separately under Settings → Indexers using the Torznab URL Prowlarr exposes for that indexer and the appropriate API key.

Each indexer can be scoped to:

```text
Movies
Shows
Both
```

Interactive Search fans out to all eligible enabled indexers concurrently. One slow or failed indexer does not discard successful results from the others.

All results remain manually downloadable regardless of Custom Format score or minimum-quality warnings.
