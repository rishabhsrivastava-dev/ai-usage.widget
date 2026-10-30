# AI Usage · Übersicht Widget

A macOS desktop widget for [Übersicht](https://tracesof.net/uebersicht/) that shows your
**Claude Code** and **OpenAI Codex** subscription usage. It keeps two kinds of data strictly apart:

1. **Official limits:** percent used and reset time, as reported by the vendors' own CLIs.
2. **Estimated tokens:** read from local logs by [`ccusage`](https://github.com/ryoppippi/ccusage).
   These are shown for reference only and are never compared against a quota.

The script never reads API keys or OAuth tokens itself, and no account emails are written to disk.

## What it shows

| Card | Field | Source | Kind |
|---|---|---|---|
| Claude Code | Plan (e.g. Pro), auth method, masked account | `claude auth status --json` | Official |
| Claude Code | Session (5h) and weekly % used, reset times | `claude -p /usage` (local command, no model call) | Official |
| Codex | Plan (e.g. Go), auth method, masked account | `codex app-server` → `account/read` | Official |
| Codex | Usage % per window (e.g. 30-day), reset time | `codex app-server` → `account/rateLimits/read` | Official |
| Codex | Fallback when the app-server is unreachable | last `rate_limits` snapshot in `~/.codex/sessions/**.jsonl`, shown as "as of HH:MM" | Official, possibly out of date |
| Both | Today / 7d / 30d tokens, daily chart | `ccusage <agent> daily --json` | Estimate |

Neither vendor exposes an absolute token allowance for subscriptions, so allowances are shown
as percentages only. If a value can't be retrieved, the card shows **"Usage unavailable"** with
a neutral striped bar instead of a made-up number. No costs are shown: for subscription usage,
token counts are not a separate bill.

Calculation rules:

- **Percent used:** preserved exactly as the source reports it, never capped. Over 100% shows an
  **OVER LIMIT** tag; only the bar's width stops at full.
- **Remaining:** `max(0, 100 − used)`.
- **Small values:** shown with a decimal (`0.4%`, `<0.1%`), never as a misleading `0%`.
- **Expired windows:** a snapshot whose reset time has passed is shown as unavailable.

## Requirements

- [Übersicht](https://tracesof.net/uebersicht/)
- Claude Code (CLI, or the VS Code extension, which ships the `claude` binary)
- Codex (CLI, Codex.app, or the VS Code extension, which ships the `codex` binary)
- `node` / `npx` for ccusage (Homebrew default path `/opt/homebrew/bin/npx`)
- `python3`: the system `/usr/bin/python3` (3.9) is enough

The `claude` and `codex` binaries are found on `PATH` first, then inside
`~/.vscode/extensions/…` and `/Applications/Codex.app`.

## Install

Symlink (or copy) this folder into Übersicht's widgets directory:

```bash
ln -s "$PWD" ~/Library/Application\ Support/Übersicht/widgets/ai-usage.widget
```

## Tests

```bash
/usr/bin/python3 -m unittest discover -s tests
```

The tests cover percentage and remaining-allowance maths, over-limit values, unit formatting,
missing or invalid data, expired windows, and reset-time parsing across time zones and the new year.

## Troubleshooting

- Inspect the raw output:
  `/usr/bin/python3 ~/Library/Application\ Support/Übersicht/widgets/ai-usage.widget/fetch-usage.py`
- If the Codex card shows "as of HH:MM", the live app-server call failed. The reason is appended to
  `~/.cache/ai-usage-widget/codex-live-error.log`.
- Claude's `/usage` output is plain text. If a future Claude Code release changes its wording,
  the card shows "Unavailable" rather than guessing.

## Credits & license

Based on an Apache-2.0 licensed Übersicht widget. Modified in 2026 by Rishabh Srivastava:
official limit sources, removal of estimated quotas and costs, glass UI restyle, English-only
text, and tests.

[Apache-2.0](LICENSE)
