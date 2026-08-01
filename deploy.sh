#!/bin/bash
# =============================================================================
# deploy.sh — Deploy the Poudre map viewer
#
#   ./deploy.sh                 # lan (default)
#   ./deploy.sh lan
#   ./deploy.sh public
#   ./deploy.sh public --dry-run
#
# Only web/ is deployed. An earlier manual `rsync *` from the project root put
# the entire repo — 217 MB of DEM rasters, GeoPackages and derived GeoJSON —
# onto the public host with index.html one level too deep. Sourcing from web/
# and nowhere else is what prevents a repeat.
#
# --delete is scoped by the guard below: every target's REMOTE_DIR must end in
# /poudremap, so a prune can never escape into the docroot. Do not remove it.
# =============================================================================

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$HERE/web/"

TARGET="lan"
DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    lan|public) TARGET="$arg" ;;
    --dry-run)  DRY_RUN=true ;;
    *) echo "unknown argument: $arg"; echo "usage: $0 [lan|public] [--dry-run]"; exit 2 ;;
  esac
done

case "$TARGET" in
  lan)
    REMOTE_HOST="root@homeweb.lan"
    SSH_CMD="ssh"
    APP_DIR="poudremap"
    REMOTE_DIR="/usr/share/caddy/$APP_DIR/"
    TEST_URL="http://homeweb.lan/$APP_DIR/"
    ;;
  public)
    # HostGator shared hosting, SSH on 2222.
    #
    # ~/public_html is the docroot for gregory-allen.com, www.gregory-allen.com
    # AND reederweb.com — all three serve the same tree. The per-domain
    # subdirectories under public_html (gregory-allen.com/, RamblingRoos.net/)
    # look like addon-domain docroots and are not: cPanel's userdata lists
    # stale entries for them. Verified empirically — ~/public_html/arrowhead
    # answers 200 on all three hostnames.
    #
    # Directory name is poudreweb here and poudremap on the LAN. Deliberate:
    # the LAN URL is already in use and renaming it would break it.
    REMOTE_HOST="regaroot@192.185.18.15"
    SSH_CMD="ssh -p 2222"
    APP_DIR="poudreweb"
    REMOTE_DIR="/home4/regaroot/public_html/$APP_DIR/"
    # Apex redirects to itself without www stripped; www is the canonical form
    # that answers 200 directly.
    TEST_URL="https://www.gregory-allen.com/$APP_DIR/"
    ;;
esac

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
ok()   { echo -e "${GREEN}  ✓  $*${RESET}"; }
warn() { echo -e "${YELLOW}  ⚠  $*${RESET}"; }
fail() { echo -e "${RED}  ✗  $*${RESET}"; }

echo ""
echo "  Poudre Map — Deploy [$TARGET]"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  ─────────────────────────────────"
$DRY_RUN && warn "Dry run — no files will be transferred"

# ── Guard: --delete must never be able to reach a docroot ────────────────────
# The target's own app directory name has to be the last path element. Both
# hosts serve a shared docroot with other sites in it, so a typo'd REMOTE_DIR
# with --delete would be destructive well beyond this project.
case "$REMOTE_DIR" in
  */"$APP_DIR"/) ;;
  *) fail "REMOTE_DIR must end in /$APP_DIR/ — refusing to rsync --delete into '$REMOTE_DIR'"
     exit 1 ;;
esac
[[ "$APP_DIR" == "poudremap" || "$APP_DIR" == "poudreweb" ]] || {
  fail "unexpected APP_DIR '$APP_DIR'"; exit 1; }

for cmd in rsync curl ssh; do
  command -v "$cmd" &>/dev/null || { fail "Required tool not found: $cmd"; exit 1; }
done
[[ -d "$SOURCE_DIR" ]] || { fail "Source directory not found: $SOURCE_DIR"; exit 1; }

for f in index.html poudre.pmtiles labels.json vendor/maplibre-gl.js vendor/pmtiles.js; do
  [[ -f "$SOURCE_DIR$f" ]] || { fail "Missing $f — run: make tiles"; exit 1; }
done
ok "Source: web/  ($(du -sh "$SOURCE_DIR" | cut -f1))"
ok "Target: $REMOTE_HOST:$REMOTE_DIR"
ok "URL:    $TEST_URL"
echo ""

RSYNC_OPTS=(--archive --checksum --delete --human-readable --verbose
            --exclude '.DS_Store')
# Compression is worth it over the WAN; the LAN link is faster than gzip.
[[ "$TARGET" == "public" ]] && RSYNC_OPTS+=(--compress)
$DRY_RUN && RSYNC_OPTS+=(--dry-run)

$DRY_RUN || $SSH_CMD "$REMOTE_HOST" "mkdir -p '$REMOTE_DIR'" || {
  fail "Could not create $REMOTE_DIR"; exit 1; }

echo "  Deploying files..."
echo ""
rsync "${RSYNC_OPTS[@]}" -e "$SSH_CMD" "$SOURCE_DIR" "$REMOTE_HOST:$REMOTE_DIR"
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

HTTP_CODE=$(curl -sL -o /dev/null -w "%{http_code}" --max-time 30 "$TEST_URL")
if [[ "$HTTP_CODE" == "200" ]]; then
  ok "HTTP $HTTP_CODE — page served"
else
  fail "HTTP $HTTP_CODE from $TEST_URL"
  [[ "$TARGET" == "lan" ]] && echo "      Check Caddy:  ssh $REMOTE_HOST systemctl status caddy"
  exit 1
fi

# PMTiles is read entirely through HTTP range requests. A server that answers
# 200 instead of 206 ships the whole 7 MB archive per tile read and the map
# never draws, so this check is not optional.
RANGE_CODE=$(curl -sL -o /dev/null -w "%{http_code}" --max-time 30 \
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
echo "  Verify it actually renders:"
echo "    make webcheck-url URL=$TEST_URL"
echo ""
