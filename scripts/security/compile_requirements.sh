#!/usr/bin/env bash
# scripts/security/compile_requirements.sh
#
# Regenerate the hash-pinned lockfiles from their abstract .in sources.
#
# MUST be run on the Docker target platform (linux/amd64, Python 3.12) so
# that the wheel hashes in requirements.txt match what Docker downloads.
# The easiest way to do this is via the base image:
#
#     docker run --rm \
#       -v "$(pwd):/work" -w /work \
#       python:3.12-slim \
#       bash scripts/security/compile_requirements.sh
#
# In CI (GitHub Actions) this runs on ubuntu-latest with Python 3.12,
# which is the same target as the Docker images.
#
# After running, commit the updated requirements.txt / requirements-dev.txt.
set -euo pipefail

echo "▶ installing pip-tools..."
pip install --quiet pip-tools

COMMON_FLAGS=(
    --generate-hashes
    --resolver=backtracking
    --allow-unsafe
    --strip-extras
    --quiet
)

echo "▶ compiling requirements.txt (runtime)..."
pip-compile "${COMMON_FLAGS[@]}" \
    --output-file requirements.txt \
    requirements.in

echo "▶ compiling requirements-dev.txt (dev/test)..."
pip-compile "${COMMON_FLAGS[@]}" \
    --output-file requirements-dev.txt \
    requirements-dev.in

echo "✓ lockfiles updated — review and commit:"
echo "    git add requirements.txt requirements-dev.txt"
echo "    git diff --stat HEAD -- requirements.txt requirements-dev.txt"
