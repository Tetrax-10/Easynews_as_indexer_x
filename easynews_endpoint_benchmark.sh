#!/usr/bin/env bash
# Quick A/B latency test: Easynews /2.0/search/solr-search vs /3.0/api/search.
# Also dumps the 3.0 response so we can confirm its JSON shape.
#
# Usage:
#   EASYNEWS_USER=you EASYNEWS_PASS=secret ./easynews_endpoint_benchmark.sh "the matrix" 5
#
# Runs each endpoint N times (default 3) and prints per-request time + status.
# Nothing is sent anywhere except members.easynews.com with your Basic Auth.

set -u
Q="${1:-the matrix}"
N="${2:-3}"
BASE="https://members.easynews.com"
: "${EASYNEWS_USER:?set EASYNEWS_USER}"
: "${EASYNEWS_PASS:?set EASYNEWS_PASS}"

QENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$Q")

URL_20="$BASE/2.0/search/solr-search/?fly=2&sb=1&pno=1&pby=250&u=1&chxu=1&chxgx=1&st=basic&gps=$QENC&vv=1&safeO=0&s1=relevance&s1d=-&fty%5B%5D=VIDEO"
# Best-guess 3.0 params (mirror 2.0). If this returns nothing/HTTP 404, open the
# 3.0 web UI, do a search, and copy the real request from DevTools > Network.
URL_30="$BASE/3.0/api/search/?gps=$QENC&pno=1&pby=250&fly=2&u=1&st=basic&safeO=0&s1=relevance&s1d=-&fty%5B%5D=VIDEO"

bench () {
  local label="$1" url="$2"
  echo "=== $label ==="
  for i in $(seq 1 "$N"); do
    curl -s -u "$EASYNEWS_USER:$EASYNEWS_PASS" \
      -H 'Accept: application/json, text/javascript, */*; q=0.9' \
      -w "  run $i: %{time_total}s  http=%{http_code}  bytes=%{size_download}\n" \
      -o "/tmp/ez_${label}.json" "$url"
  done
  echo "  first 600 chars of last response:"
  head -c 600 "/tmp/ez_${label}.json"; echo; echo
}

echo "Query: $Q   (x$N each)"; echo
bench "2.0" "$URL_20"
bench "3.0" "$URL_30"

echo "Saved full responses: /tmp/ez_2.0.json  /tmp/ez_3.0.json"
echo "If 3.0 looks good, paste the first ~40 lines of /tmp/ez_3.0.json back to me"
echo "so I can confirm the field names (fn / extension / rawSize / runtime / sig)."
