# CLAUDE.md

## Project
Agent Reach — Python CLI + library that gives AI agents read/search access to 15 internet platforms.
Positioning: installer + doctor + config tool. NOT a wrapper — after install, agents call upstream tools directly.
Repo: github.com/Panniantong/Agent-Reach | License: MIT | Version: 1.5.0

## Commands
- `pip install -e .` — Dev install
- `pytest tests/ -v` — All tests
- `pytest tests/test_cli.py -v` — CLI tests only
- `bash test.sh` — Full integration test (creates venv, installs, runs doctor + channel tests)
- `python -m agent_reach.cli doctor` — Run diagnostics
- `python -m agent_reach.cli install --env=auto` — Auto-configure

## Structure
- `agent_reach/cli.py` — CLI entry point (argparse)
- `agent_reach/core.py` — Core read/search routing logic
- `agent_reach/config.py` — Config management (YAML, env vars)
- `agent_reach/doctor.py` — Diagnostics engine
- `agent_reach/probe.py` — Backend health probing (real execution, not just `which`)
- `agent_reach/cookie_extract.py` — Browser cookie extraction for cookie-auth platforms
- `agent_reach/transcribe.py` — Audio/video transcription helper
- `agent_reach/channels/` — One file per platform (twitter.py, reddit.py, youtube.py, bilibili.py, xiaohongshu.py, xueqiu.py, v2ex.py, etc.)
- `agent_reach/channels/base.py` — `Channel` base class (all channels inherit from this)
- `agent_reach/backends/opencli.py` — OpenCLI backend probing (drives user's real Chrome, reuses login sessions)
- `agent_reach/integrations/mcp_server.py` — MCP server integration
- `agent_reach/skill/` — OpenClaw skill files
- `agent_reach/guides/` — Usage guides
- `agent_reach/utils/` — Shared helpers (paths, process, text)
- `tests/` — pytest tests
- `config/mcporter.json` — MCP tool config
- `scripts/sync-upstream.sh` — Upstream sync helper
- `docs/` — Translated READMEs, install/troubleshooting/cookie-export guides
- `card-scanner-app/` — Independent FastAPI + vanilla-JS web app (CardVault, encrypted card vault with Lemon Squeezy paywall). Self-contained: nothing here imports from or modifies `agent_reach/`.

## Channel contract
Channels check availability and route to backends; they do NOT wrap platform reads.
- Each channel is a single file in `channels/`, inherits from `Channel` (base.py)
- Must implement `can_handle(url)` and `check(config)`; `check()` must set `self.active_backend` (None = unavailable)
- `backends` is an ORDERED candidate list — backends[0] preferred, rest are fallbacks. Switching backends = reordering the list, not rewriting code
- Users force a backend via config key `<channel>_backend` / env `<CHANNEL>_BACKEND` (applied by `ordered_backends()`)
- `tier`: 0 = zero-config, 1 = needs free key, 2 = needs setup
- `shutil.which()` alone is NOT proof of health — really execute a lightweight command before claiming a backend active (see `agent_reach.probe`)

## Conventions
- Python 3.10+ with type hints
- Use `loguru` for logging, `rich` for CLI output
- Commit format: `type(scope): message` (one commit = one thing)
- All upstream tool calls go through public API/CLI, never hack internals

## Rules
- NEVER modify upstream open source projects' source code
- Agent Reach is a "glue layer" — only route and call, don't reimagine
- Version in THREE places must match: `pyproject.toml`, `__init__.py`, `tests/test_cli.py`
- Always new branch for changes, PR to main, never push to main directly
- Run `pytest tests/ -v` before committing — all tests must pass
- Cookie-based auth (Twitter, XHS): prefer OpenCLI (reuses Chrome login); traditional CLIs use Cookie-Editor export only, no QR scan
- XHS login: Cookie-Editor browser export only (QR will hang)
- `opencli doctor` auto-starts the daemon (side effect) — health checks must use `opencli daemon status`

---

<!-- Appended from https://raw.githubusercontent.com/forrestchang/andrej-karpathy-skills/main/CLAUDE.md on 2026-07-29. Everything below this line is third-party general coding guidance; the Agent Reach project rules above take precedence. -->

## General coding guidelines

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
