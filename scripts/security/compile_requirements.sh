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
#
# Pass --upgrade to pull newer versions within the bounds set in requirements.in:
#
#     bash scripts/security/compile_requirements.sh --upgrade
#
# This matters more than it looks. Without it, pip-compile treats the versions
# already pinned in the output file as constraints and keeps them — so adding one
# dependency leaves every other package frozen at whatever it was, and a published
# security fix never arrives no matter how often the script is run. The CI lockfile
# job runs *without* --upgrade on purpose, because its question is "does the lock
# match the sources", not "is there something newer".
set -euo pipefail

UPGRADE=()
if [ "${1:-}" = "--upgrade" ]; then
    UPGRADE=(--upgrade)
    echo "▶ upgrade mode: resolving to the newest versions allowed by requirements.in"
fi

echo "▶ installing pip-tools..."
pip install --quiet pip-tools

COMMON_FLAGS=(
    --generate-hashes
    --resolver=backtracking
    --allow-unsafe
    --strip-extras
    --quiet
    "${UPGRADE[@]+"${UPGRADE[@]}"}"
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
