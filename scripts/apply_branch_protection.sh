#!/usr/bin/env bash
# Apply branch-protection rules to `main` on Arkalogy/best-photo-picker.
#
# Branch protection is a Free-tier feature on PUBLIC repos only. While
# the repo is private this script will exit 403 (`Upgrade to GitHub
# Pro or make this repository public to enable this feature`). Run it
# once the public flip lands — it's idempotent (PUT semantics replace
# the entire ruleset, so re-running matches the current state).
#
# What this enforces:
#
#   * PRs to main require approving review (count: 1, dismiss stale
#     reviews on new commits, no codeowner-only approval — the OSS
#     repo doesn't have CODEOWNERS configured).
#   * Status checks: `lint` (CI lint job) and `test` (CI test job)
#     must pass before merge. `stress` (slow tests) and `e2e` are
#     intentionally NOT required — flakes there shouldn't block
#     feature PRs; failures get triaged separately.
#   * Strict mode: branch must be up-to-date with main before merge.
#   * Linear history: no merge commits — rebase or squash only.
#   * Force pushes blocked. Direct pushes to main blocked even for
#     admins; everyone goes through PR review.
#
# Re-running is safe. If you need to change a rule, edit this script
# and run again — the API replaces the full ruleset on each PUT.
#
# Usage:
#   ./scripts/apply_branch_protection.sh
#
# Pre-reqs:
#   * `gh` CLI authenticated as a repo admin.
#   * Repo flipped public (or upgraded to GitHub Pro on private).

set -euo pipefail

REPO="Arkalogy/best-photo-picker"
BRANCH="main"

if ! gh auth status &>/dev/null; then
    echo "ERROR: gh CLI not authenticated. Run \`gh auth login\` first." >&2
    exit 1
fi

echo "Applying branch protection to $REPO/$BRANCH ..."

gh api -X PUT \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "/repos/$REPO/branches/$BRANCH/protection" \
    --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["lint", "test"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1,
    "require_last_push_approval": false
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": true
}
JSON

echo
echo "Branch protection applied. Verify at:"
echo "  https://github.com/$REPO/settings/branches"
