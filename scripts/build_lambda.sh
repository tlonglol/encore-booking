#!/usr/bin/env bash
# Build the Lambda deployment package into build/lambda. infra/lambda.tf zips that directory.
#
# The wheels are Linux x86_64, not this Mac's: psycopg and pydantic-core ship compiled code, and a
# macOS wheel in the zip is an ImportError on the first request. LAMBDA_ARCH below and
# `architectures` in infra/lambda.tf are the same choice written twice; change both or neither.
set -euo pipefail

cd "$(dirname "$0")/.."
BUILD_DIR="build/lambda"
LAMBDA_ARCH="manylinux2014_x86_64"
PYTHON_VERSION="3.12"

echo "Cleaning $BUILD_DIR"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

echo "Installing $LAMBDA_ARCH wheels for Python $PYTHON_VERSION"
# --only-binary=:all: makes a missing wheel an error here rather than a broken source build in the zip.
pip install \
  -r requirements-lambda.txt \
  --platform "$LAMBDA_ARCH" \
  --implementation cp \
  --python-version "$PYTHON_VERSION" \
  --only-binary=:all: \
  --target "$BUILD_DIR" \
  --quiet

echo "Copying application code and frontend"
cp -r app "$BUILD_DIR"/
cp -r frontend "$BUILD_DIR"/

# alembic/ is deliberately absent: migrations run from the laptop against Neon's direct connection
# string, so nothing in the bundle can run DDL against the database a request is using.
find "$BUILD_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -type d -name "tests" -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -name "*.pyc" -delete 2>/dev/null || true

# The handler Terraform points at, and the frontend shell the /book/* catch-all returns. If either is
# missing the deploy answers 500 on the first request instead of failing here, so check now.
test -f "$BUILD_DIR/app/lambda_handler.py" || { echo "FAIL: app/lambda_handler.py missing"; exit 1; }
test -f "$BUILD_DIR/frontend/book.html" || { echo "FAIL: frontend/book.html missing"; exit 1; }
test -d "$BUILD_DIR/mangum" || { echo "FAIL: mangum not installed into the bundle"; exit 1; }

echo "Built $BUILD_DIR ($(du -sh "$BUILD_DIR" | cut -f1) unpacked)"
