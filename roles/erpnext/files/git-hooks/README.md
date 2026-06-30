# System-Wide Git Hooks & Security Guidelines

This directory contains system-wide Git hooks that enforce security best practices across all users (including `root`, `frappe`, `iiab-admin`) and all repositories on this IIAB box.

## Standard Operating Security Rules (Rules 1-6)

1. **Never Embed PATs in `.git/config`**
   - Credentials must never be saved in local repository configuration files.
   - Checked dynamically by both `pre-commit` and `pre-push` hooks.

2. **Use Credential Helpers**
   - Use `gh auth git-credential` (GitHub CLI) or `git credential-store`.
   - The credential store file (`~/.git-credentials`) must be locked down:
     - `chmod 600 ~/.git-credentials`
     - Verified and secured automatically by the `post-commit` hook.

3. **Audit Before Every Commit**
   - Active scanning of staged changes, commit messages, and diff history for patterns matching GitHub PATs:
     - `github_pat_[a-zA-Z0-9_]+`
     - `ghp_[a-zA-Z0-9]{36}` (Classic token)
     - `gho_[a-zA-Z0-9]{36}` (OAuth token)
     - `ghu_`, `ghs_`, `ghr_` (User, Server, Runner tokens)
   - Checked and enforced strictly by the `pre-commit` hook.

4. **Scope PATs with Minimum Privileges**
   - Use fine-grained Personal Access Tokens.
   - Limit scopes to repository push/pull access only.
   - Set expiration to maximum 90 days.

5. **Consistent Git Config Identity**
   - The committer identity must be consistent across all environments:
     - Name: `Blondel Mondesir`
     - Email: `blondel.md@gmail.com`
   - Commits with other identities are strictly blocked by the `pre-commit` hook.

6. **Agent Operational Directives (For AI Coding Assistants)**
   - Never use `-u` with PAT URLs (e.g. `git push -u https://<token>@github.com...`).
   - Never log PATs or write them to temporary files.
   - Always clean up environment variables and config fields immediately after push operations.

## Hook Files

- **`pre-commit`**: Checks identity, scans staged text changes, local config, and remote URLs for credentials.
- **`post-commit`**: Sets `chmod 600` on `.git/config` and `~/.git-credentials` to prevent exposure.
- **`pre-push`**: Scans commit history/messages for PATs prior to pushing to remote repositories.

---
*Managed by IIAB Security Hardening*
