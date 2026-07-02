# CLAUDE.md

How this service works and ships — only what isn't obvious from the code.
For *what changed and why*, read `git log` (commit messages are the changelog).
For *configuration*, read `.env.example` — the authoritative, annotated list of
every knob. Nothing from it is duplicated here, so it can't drift.

## What this is

A Newznab-API bridge: Usenet automation tools (NZBHydra2, Prowlarr, Sonarr,
Radarr) search **Easynews** as if it were a Newznab indexer. The repo is the
artifact — no build step beyond the Docker image.

- `server.py` — Flask app, `/api` (Newznab `t=caps|search|movie|tvsearch|get`):
  query translation, result filtering/mapping to RSS, `.nzb` downloads.
- `easynews_client.py` — `EasynewsClient`: login, hedged search, NZB download.
- `query_replace.py` — per-title query rewrites (`EASYNEWS_QUERY_REPLACE`).


## Deploy

- Push to `main` → GitHub Action builds
  `ghcr.io/lystad93/easynews_as_indexer_x:latest`.
- VPS (compose service `easynews-indexer`):
  `docker compose --profile easynews-indexer pull && docker compose --profile easynews-indexer up -d`


## Verify locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m py_compile server.py easynews_client.py query_replace.py
EASYNEWS_USER=… EASYNEWS_PASS=… NEWZNAB_APIKEY=key .venv/bin/python server.py
curl 'http://localhost:8081/api?t=caps'
curl 'http://localhost:8081/api?t=search&q=matrix&apikey=key'
```

## Where the design rationale lives

Why-comments sit next to the code they explain — read them before changing
that area:

- Hedged/latency-bounded search, trust-empty, degraded tagging —
  `easynews_client.py: search_hedged`
- Endpoint 2.0 vs 3.0, URL building, advanced search, sorting —
  `easynews_client.py: _build_search_url*` (A/B helper:
  `easynews_endpoint_benchmark.sh`)
- Non-blocking login refresh + startup warm-up — `server.py: client()`,
  `_refresh_login_async`
- Result cache and what must never be cached — `server.py: _SEARCH_CACHE` and
  the cache-put site in the `/api` handler
- 0-result fallback (spelling variants, alt titles) —
  `server.py: _run_fallback_search`, `_spelling_variants`
- Season-only queries, title filters, category/anime detection —
  `server.py: _SEASON_ONLY_RE`, `filter_and_map`, `_detect_anime`
- Language/codec metadata attrs (NZBHydra must pass attrs through to
  downstream clients) — `server.py`, the `META_*` block

## Conventions

- Write descriptive commit messages — they are this project's changelog.
- End commit messages with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
