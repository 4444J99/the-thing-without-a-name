#!/usr/bin/env bash
# Build the danse Vision extractor.
#
# Zero packages: Vision.framework ships in the Command Line Tools SDK, so this needs
# nothing installed beyond Xcode CLT. Verified present at
# $(xcrun --show-sdk-path)/System/Library/Frameworks/Vision.framework/Modules.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "swiftc not found — install Xcode Command Line Tools: xcode-select --install" >&2
  exit 1
fi

# Beta Command Line Tools can leave the unversioned SDK pointing at the next
# macOS major while `swiftc` still matches the running major's SDK. Prefer that
# compatible SDK when it is installed; DANSE_SDK remains an explicit override.
DANSE_DEFAULT_SDK="$(xcrun --sdk macosx --show-sdk-path)"
DANSE_SDK_DIR="$(dirname "$DANSE_DEFAULT_SDK")"
DANSE_OS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
DANSE_COMPAT_SDK="$DANSE_SDK_DIR/MacOSX$DANSE_OS_MAJOR.sdk"
DANSE_SELECTED_SDK="${DANSE_SDK:-$DANSE_DEFAULT_SDK}"
if [[ -z "${DANSE_SDK:-}" && -d "$DANSE_COMPAT_SDK" ]]; then
  DANSE_SELECTED_SDK="$DANSE_COMPAT_SDK"
fi
if [[ ! -d "$DANSE_SELECTED_SDK" ]]; then
  echo "macOS SDK not found: $DANSE_SELECTED_SDK" >&2
  exit 1
fi

# Keep compiler caches in disposable local storage. This also makes the build
# usable from a restricted worktree where the user's global cache is read-only.
DANSE_MODULE_CACHE="${DANSE_SWIFT_MODULE_CACHE:-${TMPDIR:-/tmp}/danse-swift-module-cache}"
mkdir -p "$DANSE_MODULE_CACHE/clang" "$DANSE_MODULE_CACHE/swift"

CLANG_MODULE_CACHE_PATH="$DANSE_MODULE_CACHE/clang" \
SWIFT_MODULECACHE_PATH="$DANSE_MODULE_CACHE/swift" \
swiftc -O \
  -sdk "$DANSE_SELECTED_SDK" \
  -framework Vision \
  -framework AppKit \
  -framework CoreImage \
  -o danse-vision \
  main.swift

echo "sdk: $DANSE_SELECTED_SDK"
echo "built: $(pwd)/danse-vision"
