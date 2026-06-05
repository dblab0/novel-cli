#!/bin/bash
# Build frontend and copy to static directory

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Building web frontend..."
cd "$PROJECT_ROOT/web"
npm install --registry=https://registry.npmmirror.com
npm run build

echo "Copying web build to static directory..."
mkdir -p "$PROJECT_ROOT/src/novel_cli/web/static"
cp -r "$PROJECT_ROOT"/web/dist/* "$PROJECT_ROOT/src/novel_cli/web/static/"

echo "Web frontend build complete!"

# Build vis frontend (optional)
if [ -d "$PROJECT_ROOT/vis" ]; then
    echo "Building vis frontend..."
    cd "$PROJECT_ROOT/vis"
    npm install --registry=https://registry.npmmirror.com
    npm run build

    echo "Copying vis build to static directory..."
    mkdir -p "$PROJECT_ROOT/src/novel_cli/vis/static"
    cp -r "$PROJECT_ROOT"/vis/dist/* "$PROJECT_ROOT/src/novel_cli/vis/static/"

    echo "Vis frontend build complete!"
fi