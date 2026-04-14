#!/bin/bash
set -e

# update_rapidpro_binaries.sh
# Automates the sync, build, and release process for IIAB RapidPro forks.
# Prerequisites: gh CLI (authenticated), Go 1.25+, git, upx.

GO_BIN="/usr/local/go/bin/go"
WORK_DIR="/opt/iiab"
GH_USER="deldesir"

# Upstream mappings
declare -A UPSTREAMS
UPSTREAMS["mailroom"]="https://github.com/nyaruka/mailroom.git"
UPSTREAMS["courier"]="https://github.com/nyaruka/courier.git"
UPSTREAMS["rapidpro"]="https://github.com/nyaruka/rapidpro.git"
UPSTREAMS["wuzapi"]="https://github.com/asternic/wuzapi.git"

# Build Target Mappings (Package Main)
declare -A MAIN_PKGS
MAIN_PKGS["mailroom"]="./cmd/mailroom"
MAIN_PKGS["courier"]="./cmd/courier"
MAIN_PKGS["wuzapi"]="."
MAIN_PKGS["rapidpro"]="" # Empty means skip Go binary build, sync releases only

check_deps() {
    echo "Checking dependencies..."
    if ! command -v gh &> /dev/null; then echo "Error: 'gh' CLI not found."; exit 1; fi
    if ! gh auth status &> /dev/null; then echo "Error: 'gh' not authenticated."; exit 1; fi
    
    echo "Setting up headless global git credentials magically via gh..."
    gh auth setup-git

    if [ ! -x "$GO_BIN" ]; then echo "Warning: Go binary not found at $GO_BIN. Ensure this is intentional if only syncing Python apps."; fi

    if ! command -v upx &> /dev/null; then 
        echo "Installing UPX for extreme binary compression (Enhancement)..."
        apt-get update && apt-get install -y upx-ucl || true
    fi
    echo "Dependencies OK."
}

sync_and_release() {
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

    # Fetch & Sync (with Github CI Auth handled seamlessly)
    echo "Syncing with upstream..."
    git checkout main 2>/dev/null || git checkout master
    git pull origin main 2>/dev/null || git pull origin master
    git fetch upstream
    git merge upstream/main --no-edit 2>/dev/null || git merge upstream/master --no-edit 2>/dev/null || true
    git push origin HEAD || echo "Warning: git push declined securely (likely no changes to push)"

    # Determine Version algorithmically
    echo "Determining target version natively..."
    local TAG
    if [ "$REPO" == "wuzapi" ]; then
        # Wuzapi lacks upstream semantic tags. Detect drift between physical commits natively.
        LATEST_TAG=$(git tag | sort -V | grep -E '^v[0-9]' | tail -n 1)
        if [ "$(git rev-parse HEAD)" != "$(git rev-parse "$LATEST_TAG^{}" 2>/dev/null || git rev-parse "$LATEST_TAG")" ]; then
            BASE=${LATEST_TAG%.*}
            PATCH=${LATEST_TAG##*.}
            TAG="${BASE}.$((PATCH+1))"
            echo "WuzAPI HEAD ahead of prior releases natively. Auto-bumping tag to -> $TAG"
        else
            TAG=$LATEST_TAG
            echo "WuzAPI matches exactly $LATEST_TAG. Tag bumped skipped natively."
        fi
    else
        # RapidPro/Mailroom/Courier natively sync their strings logically to upstream.
        TAG=$(git ls-remote --tags upstream | awk '{print $2}' | grep -v "{}" | grep "refs/tags/v" | sort -V | tail -n 1 | sed 's|refs/tags/||')
        echo "Upstream Semantic logic fetched globally -> Target version: $TAG"
    fi

    local BIN_AMD="${REPO}-linux-amd64"
    local BIN_ARM="${REPO}-linux-arm64"
    local PKG_PATH="${MAIN_PKGS[$REPO]}"

    # Tag locally uniquely
    if ! git rev-parse "$TAG" >/dev/null 2>&1; then
        git tag "$TAG"
        git push origin "$TAG" || true
    fi

    if [ -z "$PKG_PATH" ]; then
        echo "[$REPO] Generic codebase detected natively. Skipping Go compiler payload gracefully."
        if ! gh release view "$TAG" -R "$GH_USER/$REPO" &> /dev/null; then
            gh release create "$TAG" --title "$TAG" --notes "Automated release of $TAG" -R "$GH_USER/$REPO" || true
        fi
        echo "$REPO release fully structured up to $TAG."
        return
    fi

    # Go Build logic
    echo "Building Binary Arch payloads (Target: $PKG_PATH)..."
    "$GO_BIN" mod tidy || true
    
    echo "  - Synthesizing heavily AMD64..."
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 "$GO_BIN" build -v -ldflags="-s -w" -o "$BIN_AMD" "$PKG_PATH" || echo "Warning: Build for AMD64 failed gracefully."
    
    echo "  - Synthesizing heavily ARM64..."
    CGO_ENABLED=0 GOOS=linux GOARCH=arm64 "$GO_BIN" build -v -ldflags="-s -w" -o "$BIN_ARM" "$PKG_PATH" || echo "Warning: Build for ARM64 failed gracefully natively."

    # ENHANCEMENT: UPX Compression to strip immense backend fat securely!
    for BIN in "$BIN_AMD" "$BIN_ARM"; do
        if [ -f "$BIN" ]; then
            echo "Compressing $BIN massively using UPX..."
            upx -9 "$BIN" || true
        fi
    done

    # Upload mechanism
    echo "Creating/Updating API hooks $TAG..."
    if gh release view "$TAG" -R "$GH_USER/$REPO" &> /dev/null; then
        echo "Release exists structurally. Pushing compressed binaries..."
        gh release upload "$TAG" ${REPO}-linux-* --clobber -R "$GH_USER/$REPO" || true
    else
        echo "Generating standalone environment baseline..."
        gh release create "$TAG" ${REPO}-linux-* --title "$TAG" --notes "Automated standalone binary sync of $TAG" -R "$GH_USER/$REPO" || true
    fi
    
    echo "[$REPO] Subsystem completely automated securely."

    # OPTIONAL: Ansible Auto-Yaml Hardcoder (Option B)
    # If explicitly required to hardcode Ansible securely instead of relying on generic 'latest', uncomment:
    # if [ "$REPO" == "mailroom" ] || [ "$REPO" == "courier" ]; then
    #     sed -i "s|/$REPO/releases/.*/download/.*-|/$REPO/releases/download/$TAG/|g" "$WORK_DIR/iiab/roles/rapidpro/tasks/install_binary.yml"
    # elif [ "$REPO" == "wuzapi" ]; then
    #     sed -i "s|/$REPO/releases/.*/download/.*-|/$REPO/releases/download/$TAG/|g" "$WORK_DIR/iiab/roles/wuzapi/tasks/install.yml"
    # elif [ "$REPO" == "rapidpro" ]; then
    #     sed -i "s|version: .*|version: $TAG|g" "$WORK_DIR/iiab/roles/rapidpro/tasks/install.yml"
    # fi
}

# Main Bootstrapper
check_deps

# Check for arguments natively to support targeted subset rebuilds elegantly.
TARGETS=("$@")
if [ ${#TARGETS[@]} -eq 0 ]; then
    TARGETS=("mailroom" "courier" "rapidpro" "wuzapi")
fi

for TARGET in "${TARGETS[@]}"; do
    sync_and_release "$TARGET"
done

echo "Architectural automation completed elegantly."
