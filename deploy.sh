#!/bin/bash
# =============================================================================
# deploy.sh — Deploy the Poudre map viewer to homeweb.lan/poudremap
#
# Modelled on ~/Work/HomeWebSource/deploy.sh, with one important difference:
# that script rsyncs --delete straight into /usr/share/caddy/, which is the
# docroot itself. This one targets a subdirectory, so --delete only ever
# prunes inside /usr/share/caddy/poudremap/ and cannot touch the main site.
# The guard below enforces that; do not remove it.
#
# Usage:
#   ./deploy.sh              # deploy and verify
#   ./deploy.sh --dry-run    # show what would be sent, no changes made
# =============================================================================

set -uo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/web/"
REMOTE_HOST="root@homeweb.lan"
REMOTE_DIR="/usr/share/caddy/poudremap/"
TEST_URL="http://homeweb.lan/poudremap/"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
ok()   { echo -e "${GREEN}  ✓  $*${RESET}"; }
warn() { echo -e "${YELLOW}  ⚠  $*${RESET}"; }
fail() { echo -e "${RED}  ✗  $*${RESET}"; }

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && { DRY_RUN=true; warn "Dry run — no files will be transferred"; }

echo ""
echo "  Poudre Map — Deploy"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  ─────────────────────────────────"

# ── Guard: never let --delete loose on the docroot ───────────────────────────
case "$REMOTE_DIR" in
  */poudremap/) ;;
  *) fail "REMOTE_DIR must end in /poudremap/ — refusing to rsync --delete into '$REMOTE_DIR'"
     exit 1 ;;
esac

for cmd in rsync curl ssh; do
  command -v "$cmd" &>/dev/null || { fail "Required tool not found: $cmd"; exit 1; }
done

[[ -d "$SOURCE_DIR" ]] || { fail "Source directory not found: $SOURCE_DIR"; exit 1; }

# ── Preflight: the artifacts that make the map work ──────────────────────────
for f in index.html poudre.pmtiles vendor/maplibre-gl.js vendor/pmtiles.js; do
  [[ -f "$SOURCE_DIR$f" ]] || { fail "Missing $f — run: make tiles"; exit 1; }
done
ok "Source: $SOURCE_DIR"
ok "Tiles:  $(du -h "$SOURCE_DIR/poudre.pmtiles" | cut -f1)"
ok "Target: $REMOTE_HOST:$REMOTE_DIR"
echo ""

RSYNC_OPTS=(--archive --checksum --delete --human-readable --verbose
            --exclude '.DS_Store')
$DRY_RUN && RSYNC_OPTS+=(--dry-run)

$DRY_RUN || ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_DIR'" || {
  fail "Could not create $REMOTE_DIR"; exit 1; }

echo "  Deploying files..."
echo ""
rsync "${RSYNC_OPTS[@]}" -e ssh "$SOURCE_DIR" "$REMOTE_HOST:$REMOTE_DIR"
RSYNC_EXIT=$?
echo ""
[[ $RSYNC_EXIT -eq 0 ]] || { fail "Rsync failed (exit $RSYNC_EXIT)"; exit $RSYNC_EXIT; }
ok "Files deployed"

if $DRY_RUN; then
  warn "Dry run complete — skipping verification"
  echo ""
  exit 0
fi

# ── Verify ───────────────────────────────────────────────────────────────────
echo ""
echo "  Verifying $TEST_URL ..."

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$TEST_URL")
if [[ "$HTTP_CODE" == "200" ]]; then
  ok "HTTP $HTTP_CODE — page served"
else
  fail "HTTP $HTTP_CODE from $TEST_URL"
  echo "      Check Caddy:  ssh $REMOTE_HOST systemctl status caddy"
  exit 1
fi

# PMTiles is read entirely through HTTP range requests. A server that answers
# 200 instead of 206 serves the whole 7 MB archive per tile read and the map
# never draws, so this check is not optional.
RANGE_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 \
  -H "Range: bytes=0-1023" "${TEST_URL}poudre.pmtiles")
if [[ "$RANGE_CODE" == "206" ]]; then
  ok "HTTP $RANGE_CODE — range requests supported (PMTiles will work)"
else
  fail "HTTP $RANGE_CODE on a Range request — expected 206"
  echo "      PMTiles cannot stream from this server; the map will not draw."
  exit 1
fi

echo ""
ok "Deployed: $TEST_URL"
echo ""
