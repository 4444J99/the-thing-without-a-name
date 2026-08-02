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

swiftc -O \
  -framework Vision \
  -framework AppKit \
  -framework CoreImage \
  -o danse-vision \
  main.swift

echo "built: $(pwd)/danse-vision"
