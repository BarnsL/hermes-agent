#!/usr/bin/env bash
# Canonical test runner for Hermes desktop
set -e
cd "$(dirname "$0")/.."

echo "=== tsc ==="
npx tsc --noEmit

echo "=== vitest (sidebar) ==="
npx vitest run --environment jsdom src/app/chat/sidebar/ src/store/

echo "=== build ==="
npm run build
