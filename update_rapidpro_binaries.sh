#!/bin/bash
set -e

# update_rapidpro_binaries.sh
# Automates the sync, build, and release process for IIAB RapidPro forks.
# Prerequisites: gh CLI (authenticated), Go 1.25+ (in /usr/local/go), git.

GO_BIN="/usr/local/go/bin/go"
WORK_DIR="/opt/iiab"
GH_USER="deldesir" 
# Upstream mappings
declare -A UPSTREAMS
UPSTREAMS["mailroom"]="https://github.com/nyaruka/mailroom.git"
UPSTREAMS["courier"]="https://github.com/nyaruka/courier.git"
UPSTREAMS["wuzapi"]="https://github.com/asternic/wuzapi.git"

# Build Target Mappings (Package Main)
declare -A MAIN_PKGS
MAIN_PKGS["mailroom"]="./cmd/mailroom"
MAIN_PKGS["courier"]="./cmd/courier"
MAIN_PKGS["wuzapi"]="."

check_deps() {
    echo "Checking dependencies..."
    if ! command -v gh &> /dev/null; then echo "Error: 'gh' CLI not found."; exit 1; fi
    if ! gh auth status &> /dev/null; then echo "Error: 'gh' not authenticated."; exit 1; fi
    if [ ! -x "$GO_BIN" ]; then echo "Error: Go binary not found at $GO_BIN"; exit 1; fi
    echo "Dependencies OK."
}

sync_and_build() {
    local REPO=$1
    echo "--------------------------------------------------------"
    echo "Processing $REPO..."
    
    cd "$WORK_DIR"
    if [ ! -d "$REPO" ]; then
        echo "Cloning $REPO..."
        git clone "https://github.com/$GH_USER/$REPO"
    fi
    cd "$REPO"

    # Configure Remote
    if ! git remote get-url upstream &> /dev/null; then
        echo "Adding upstream remote..."
        git remote add upstream "${UPSTREAMS[$REPO]}"
    fi

    # Fetch & Sync
    echo "Syncing with upstream..."
    git fetch upstream
    git checkout main || git checkout master
    git merge upstream/main --no-edit || git merge upstream/master --no-edit
    git push origin HEAD

    # Determine Version
    echo "Determining target version..."
    local TAG
    if [ "$REPO" == "wuzapi" ]; then
        # Wuzapi: Increment local Patch version
        LAST_TAG=$(git tag | sort -V | tail -n 1)
        # Simple increment logic (needs manual oversight usually, but scripting for now based on pattern)
        # Using basic semver increment of PATCH
        if [[ $LAST_TAG =~ v([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
             MAJOR="${BASH_REMATCH[1]}"
             MINOR="${BASH_REMATCH[2]}"
             PATCH="${BASH_REMATCH[3]}"
             NEW_PATCH=$((PATCH + 1))
             TAG="v$MAJOR.$MINOR.$NEW_PATCH"
        else
             TAG="v1.0.1" # Fallback
        fi
        echo "Custom Lifecycle detected. Next version: $TAG"
    else
        # Mailroom/Courier: Align with Upstream
        TAG=$(git ls-remote --tags upstream | awk '{print $2}' | grep -v "{}" | grep "refs/tags/v" | sort -V | tail -n 1 | sed 's|refs/tags/||')
        echo "Upstream Alignment detected. Target version: $TAG"
    fi

    local BIN_AMD="${REPO}-linux-amd64"
    local BIN_ARM="${REPO}-linux-arm64"

    # Build
    echo "Building Binaries (Target: ${MAIN_PKGS[$REPO]})..."
    "$GO_BIN" mod tidy || true # Try to tidy, ignore failure if 1.25 strictness issues on tidy but not build
    
    echo "  - Building AMD64..."
    GOOS=linux GOARCH=amd64 "$GO_BIN" build -v -o "$BIN_AMD" "${MAIN_PKGS[$REPO]}"
    
    echo "  - Building ARM64..."
    GOOS=linux GOARCH=arm64 "$GO_BIN" build -v -o "$BIN_ARM" "${MAIN_PKGS[$REPO]}"

    # Verify
    if [ ! -f "$BIN_AMD" ] || [ ! -f "$BIN_ARM" ]; then
        echo "Error: Build failed. Binaries missing."
        exit 1
    fi

    # Release
    echo "Creating/Updating Release $TAG..."
    # Tag locally if not exists
    if ! git rev-parse "$TAG" >/dev/null 2>&1; then
        git tag "$TAG"
        git push origin "$TAG"
    fi

    # Check if release exists
    if gh release view "$TAG" -R "$GH_USER/$REPO" &> /dev/null; then
        echo "Release exists. Uploading assets (clobbering)..."
        gh release upload "$TAG" "$BIN_AMD" "$BIN_ARM" --clobber -R "$GH_USER/$REPO"
    else
        echo "Creating new release..."
        gh release create "$TAG" "$BIN_AMD" "$BIN_ARM" --title "$TAG" --notes "Automated release of $TAG" -R "$GH_USER/$REPO"
    fi
    
    echo "$REPO processing complete."
}

# Main
check_deps
sync_and_build "mailroom"
sync_and_build "courier"
sync_and_build "wuzapi"

echo "All components backed up and released."
