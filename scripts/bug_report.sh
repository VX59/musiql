#!/bin/bash

set -euo pipefail

REPO_PATH="${REPO_PATH:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
GH_REPO="vx59/musiql"

cd "$REPO_PATH"

# Pull latest code
echo "[1/3] Pulling latest code..."
git pull
git submodule update --init --recursive

# Ask Claude to write issues directly to a JSON file
TMP_JSON=$(mktemp /tmp/bug_report_XXXXXX.json)
echo "[2/3] Analyzing codebase with Claude (this may take a minute)..."
claude --print "Analyze this codebase for bugs, regressions, and code quality issues.
Write the results to the file $TMP_JSON as a JSON array.
Each element must have these fields:
  title  - concise issue title under 70 characters
  body   - detailed description: what the bug is, which file/line, and how to fix it (markdown ok)
  labels - array using only these values: bug, security, code-quality, frontend, backend

Write the file and nothing else."

# Validate the file was written and contains valid JSON
if ! jq empty "$TMP_JSON" 2>/dev/null; then
    echo "ERROR: Claude did not write valid JSON to $TMP_JSON" >&2
    cat "$TMP_JSON" >&2
    rm -f "$TMP_JSON"
    exit 1
fi

ISSUE_COUNT=$(jq length "$TMP_JSON")
echo "[3/3] Claude found $ISSUE_COUNT issues. Creating GitHub issues..."

# Fetch existing open issue titles once to avoid per-issue API calls
EXISTING_TITLES=$(gh issue list --repo "$GH_REPO" --state open --limit 200 --json title -q '.[].title')

CREATED=0
SKIPPED=0

jq -c '.[]' "$TMP_JSON" | while read -r issue; do
    TITLE=$(echo "$issue" | jq -r '.title')
    BODY=$(echo "$issue"  | jq -r '.body')
    LABELS=$(echo "$issue" | jq -r '.labels | join(",")')

    if echo "$EXISTING_TITLES" | grep -qF "$TITLE"; then
        echo "  [skip] $TITLE"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    gh issue create \
        --repo "$GH_REPO" \
        --title "$TITLE" \
        --body "$BODY" \
        --label "$LABELS"

    echo "  [created] $TITLE"
    CREATED=$((CREATED + 1))
done

rm -f "$TMP_JSON"
echo "Done. Created: $CREATED  Skipped (duplicate): $SKIPPED"
