---
description: How to sync upstream nyaruka forks, rebuild Go binaries, and create GitHub releases for courier and mailroom
---

# Sync Upstream & Rebuild Binaries

## Prerequisites

- Go installed (`go version`)
- GitHub PAT with repo access (set as `$PAT`)
- AWS CLI configured (`~/.aws/credentials`)

## 1. Fetch upstream changes

```bash
# Courier
cd /opt/iiab/courier
git fetch upstream
git merge upstream/main -m "Merge upstream nyaruka/courier vX.Y.Z into main"

# Mailroom
cd /opt/iiab/mailroom
git fetch upstream
git merge upstream/main -m "Merge upstream nyaruka/mailroom vX.Y.Z into main"

# RapidPro (Python — no binary needed)
cd /opt/iiab/rapidpro
git fetch upstream
git merge upstream/main -m "Merge upstream nyaruka/rapidpro vX.Y.Z into main"
```

> [!CAUTION]
> **Tag AFTER all fork-specific commits are on `main`, not right after the merge!**
> The upstream merge commit does NOT include deldesir-specific code (WuzAPI channel, custom settings).
> If you tag the merge commit directly, `./runrole` will clone code that's MISSING the fork customizations.
>
> **Checklist before tagging RapidPro:**
> 1. Verify WuzAPI channel exists: `ls temba/channels/types/wuzapi/`
> 2. Verify `localhost:8080` webhook: `grep "localhost:8080" temba/channels/types/wuzapi/views.py`
> 3. Verify custom settings are NOT overwritten by `settings_common.py` defaults (e.g., `DYNAMO_ENDPOINT_URL`)
> 4. Only THEN tag: `git tag -f vX.Y.Z && git push origin vX.Y.Z --force`

## 2. Build stripped binaries

> [!IMPORTANT]
> Always use `-ldflags="-s -w"` to strip debug symbols. Without it, binaries are 30-40% larger.

```bash
# Courier (both architectures)
cd /opt/iiab/courier
// turbo
GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o courier-linux-amd64 ./cmd/courier
// turbo
GOOS=linux GOARCH=arm64 go build -ldflags="-s -w" -o courier-linux-arm64 ./cmd/courier

# Mailroom (both architectures)
cd /opt/iiab/mailroom
// turbo
GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o mailroom-linux-amd64 ./cmd/mailroom
// turbo
GOOS=linux GOARCH=arm64 go build -ldflags="-s -w" -o mailroom-linux-arm64 ./cmd/mailroom
```

## 3. Strip binaries

> [!IMPORTANT]
> Go's `-ldflags="-s -w"` removes DWARF but `file` may still report "not stripped". Always run `strip` after building. For arm64 cross-builds, install `binutils-aarch64-linux-gnu`.

```bash
# amd64 (native strip)
strip /opt/iiab/courier/courier-linux-amd64
strip /opt/iiab/mailroom/mailroom-linux-amd64

# arm64 (cross strip — requires: apt install binutils-aarch64-linux-gnu)
aarch64-linux-gnu-strip /opt/iiab/courier/courier-linux-arm64
aarch64-linux-gnu-strip /opt/iiab/mailroom/mailroom-linux-arm64
```

## 4. Verify binaries

```bash
# Check sizes and stripped status
for f in courier/courier-linux-{amd64,arm64} mailroom/mailroom-linux-{amd64,arm64}; do
  echo "$(basename $f): $(ls -lh /opt/iiab/$f | awk '{print $5}') $(file /opt/iiab/$f | grep -o 'not stripped\|stripped')"
done

# Smoke test amd64 (if on x86_64 host)
/opt/iiab/courier/courier-linux-amd64 --help 2>&1 | head -1
/opt/iiab/mailroom/mailroom-linux-amd64 --help 2>&1 | head -1
```

Expected sizes (stripped):
- courier-linux-amd64: ~30MB
- courier-linux-arm64: ~28MB
- mailroom-linux-amd64: ~65MB
- mailroom-linux-arm64: ~60MB

## 5. Tag and push

```bash
cd /opt/iiab/courier
git tag -f vX.Y.Z
git push origin main
git push origin vX.Y.Z --force

cd /opt/iiab/mailroom
git tag -f vX.Y.Z
git push origin main
git push origin vX.Y.Z --force
```

## 6. Create GitHub releases

```bash
export PAT="your-github-pat"

# For each repo (courier, mailroom):
REPO="deldesir/courier"  # or deldesir/mailroom
TAG="vX.Y.Z"

# Delete old release if exists
OLD_ID=$(curl -s -H "Authorization: token $PAT" "https://api.github.com/repos/$REPO/releases/tags/$TAG" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))")
[ -n "$OLD_ID" ] && curl -s -X DELETE -H "Authorization: token $PAT" "https://api.github.com/repos/$REPO/releases/$OLD_ID"

# Create release
RESP=$(curl -s -X POST -H "Authorization: token $PAT" -H "Content-Type: application/json" \
  "https://api.github.com/repos/$REPO/releases" \
  -d "{\"tag_name\":\"$TAG\",\"name\":\"$TAG\",\"body\":\"Merge upstream\"}")
UU=$(echo "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin)['upload_url'].split('{')[0])")

# Upload binaries
for arch in amd64 arm64; do
  BINARY="binary-linux-$arch"  # e.g. courier-linux-amd64
  curl -X POST -H "Authorization: token $PAT" -H "Content-Type: application/octet-stream" \
    "${UU}?name=$BINARY" --data-binary @"$BINARY"
done
```

## 7. Update Ansible role

Edit `/opt/iiab/iiab/roles/rapidpro/tasks/install_binary.yml` — update version numbers.
Edit `/opt/iiab/iiab/roles/rapidpro/tasks/install.yml` — update rapidpro `version:` tag.

Commit and push:
```bash
cd /opt/iiab/iiab
git add roles/rapidpro/
git commit -m "feat: update versions to match upstream nyaruka"
git push origin master
```

## 8. Deploy

```bash
cd /opt/iiab/iiab
./runrole rapidpro --reinstall
```
