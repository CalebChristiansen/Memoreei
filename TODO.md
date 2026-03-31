# Memoreei — Release TODO

## Pre-Release (v0.2.1)

### Code
- [ ] Commit current changes (setup CLI, FTS5 fix, README updates, questionary dep)
- [ ] Bump version in `pyproject.toml` → `0.2.1`
- [ ] Add tests for `setup` CLI command (mock questionary prompts)
- [ ] Add tests for FTS5 sanitizer edge cases (apostrophes, quotes, parens)
- [ ] Run full test suite, fix any failures
- [ ] Verify `memoreei setup` works cleanly on fresh `.env` (no existing config)

### Docs
- [ ] Update CONTRIBUTING.md — mention `memoreei setup` for dev onboarding
- [ ] Update `docs/deployment.md` — add setup command to quickstart
- [ ] Update `docs/connectors.md` — mention setup command per connector
- [ ] Add `CHANGELOG.md` — retroactive entries for v0.1.0, v0.2.0, v0.2.1

### Release Infrastructure
- [ ] Add `.github/workflows/ci.yml` — run tests on push/PR (pytest + lint)
- [ ] Add `.github/workflows/release.yml` — publish to PyPI on tag push
- [ ] Create GitHub Release for existing `v0.2.0-open-source` tag (retroactive)
- [ ] Fix repo URL in pyproject.toml (`your-org` → `CalebChristiansen`)

### Polish
- [ ] `memoreei setup` — show which connectors are already configured (✓ marker)
- [ ] `memoreei setup` — add `--reset` flag to reconfigure existing connectors
- [ ] `memoreei sync` — better progress output (spinner or progress bar)
- [ ] `memoreei` with no subcommand — show help instead of erroring
- [ ] Default DB path: consider `~/.memoreei/memoreei.db` vs `./memoreei.db` consistency

### Future (post-release)
- [ ] PyPI publishing (`python -m build && twine upload`)
- [ ] GitHub Actions badge in README
- [ ] Docker image on GHCR
- [ ] Example MCP config generator (`memoreei init-mcp`)
- [ ] OAuth flow for Gmail (replace app passwords with proper OAuth2)
