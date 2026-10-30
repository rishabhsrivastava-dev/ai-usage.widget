#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  ai-usage.widget / fetch-usage.py
# ------------------------------------------------------------
#  Two kinds of data, kept strictly apart:
#
#  1. OFFICIAL limits (percent used + reset time), read through the
#     vendors' own CLIs — no private HTTP endpoints, no credentials
#     read by this script:
#       · Claude Code → `claude auth status`  (plan / auth method)
#                       `claude -p /usage`    (local command, no model call)
#       · Codex       → `codex app-server`    JSON-RPC:
#                       account/read, account/rateLimits/read
#                       (fallback: rate_limits snapshot that Codex writes
#                        into ~/.codex/sessions/**.jsonl, shown as stale)
#     Neither source exposes an absolute token allowance, so allowances
#     are percentages only.
#
#  2. ESTIMATED tokens from local logs via ccusage (`<agent> daily --json`).
#     These are never compared against any quota.
#
#  Must stay compatible with /usr/bin/python3 3.9 (what Übersicht runs).
# ============================================================

import glob
import json
import os
import queue
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

TIMEOUT = 120  # first npx download can be slow

# Übersicht starts us via `/bin/zsh -lc`, which skips .zshrc; prepend the
# usual install locations so node/npx/codex/claude resolve.
SUBPROC_ENV = os.environ.copy()
SUBPROC_ENV["PATH"] = ":".join([
    "/opt/homebrew/bin", "/opt/homebrew/sbin",
    "/usr/local/bin", "/usr/local/sbin",
    os.path.expanduser("~/.local/bin"),
    SUBPROC_ENV.get("PATH", ""),
])

NPX = "/opt/homebrew/bin/npx"
WORK_DIR = os.path.expanduser("~/.cache/ai-usage-widget")  # cwd for CLI calls

CONFIG = {
    "claude": {
        "label": "Claude Code",
        "account_url": "https://claude.ai/settings/usage",
        "estimate_command": [NPX, "-y", "ccusage@latest", "claude", "daily", "--json"],
        "count_cache_reads": True,
    },
    "chatgpt": {
        "label": "Codex",
        "account_url": "https://chatgpt.com/codex/settings/usage",
        "estimate_command": [NPX, "-y", "ccusage@latest", "codex", "daily", "--json"],
        "count_cache_reads": True,
    },
}


# ============================================================
#  Pure helpers (covered by tests/test_fetch_usage.py)
# ============================================================
def to_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v) if "." in v else int(v)
        except ValueError:
            return None
    return None


def mask_email(email):
    """jane.doe@example.com -> ja•••@example.com"""
    if not email or "@" not in email:
        return ""
    local, domain = email.split("@", 1)
    return local[:2] + "•••@" + domain


def fmt_pct(p):
    """Never shows a misleading '0%' for small non-zero usage."""
    if p is None:
        return "—"
    if p == 0:
        return "0%"
    if abs(p) < 1:
        return ("%.1f" % p).rstrip("0").rstrip(".") + "%" if p >= 0.1 else "<0.1%"
    if abs(p) < 10 and p != int(p):
        return "%.1f%%" % p
    return "%d%%" % round(p)


def fmt_tokens(n):
    """Raw token count -> compact string. Input is always raw tokens."""
    n = int(n or 0)
    if n == 0:
        return "0"
    if n >= 1_000_000_000:
        return "%.1fB" % (n / 1e9)
    if n >= 10_000_000:
        return "%dM" % round(n / 1e6)
    if n >= 1_000_000:
        return "%.1fM" % (n / 1e6)
    if n >= 1_000:
        return "%dK" % round(n / 1e3)
    return str(n)


def window_label(mins):
    if not mins:
        return "Limit"
    mins = int(mins)
    if mins == 300:
        return "5-hour"
    if mins == 10080:
        return "Weekly"
    if mins % 1440 == 0:
        return "%d-day" % (mins // 1440)
    if mins % 60 == 0:
        return "%d-hour" % (mins // 60)
    return "%d-min" % mins


def make_limit(label, used_percent, resets_at, source, window_mins=None, as_of=None, now=None):
    """Build one verified-limit record from a source-reported percentage.

    used_percent is preserved as reported (not capped). The allowance is the
    source's 100%, so remaining = max(0, 100 - used). `bar` is the only
    clamped value and exists purely for drawing.
    """
    now = time.time() if now is None else now
    p = to_num(used_percent)
    if p is None or p < 0:
        return unavailable_limit(label, "Usage unavailable")
    p = float(p)
    r = to_num(resets_at)
    r = int(r) if r else None
    if r is not None and r <= now:
        # The window this snapshot describes has already reset; its
        # percentage no longer applies and the new value is unknown.
        return unavailable_limit(label, "Window reset since last reading")
    return {
        "label": label,
        "available": True,
        "usedPercent": p,
        "remainingPercent": max(0.0, 100.0 - p),
        "overLimit": p > 100.0,
        "pctText": fmt_pct(p),
        "remainingText": fmt_pct(max(0.0, 100.0 - p)) + " left",
        "bar": min(max(p / 100.0, 0.0), 1.0),
        "resetsAt": r,
        "windowMins": window_mins,
        "source": source,
        "asOf": int(as_of) if as_of else None,
    }


def unavailable_limit(label, reason):
    return {"label": label, "available": False, "usedPercent": None,
            "remainingPercent": None, "overLimit": False, "pctText": "—",
            "remainingText": "", "bar": 0.0, "resetsAt": None,
            "reason": reason, "source": None}


# ---------- Claude `/usage` text ----------
# Example lines (Claude Code 2.1.x):
#   Current session: 3% used · resets Oct 29 at 10:39pm (Asia/Calcutta)
#   Current week (all models): 0% used · resets Nov 5 at 10:29am (Asia/Calcutta)
USAGE_LINE = re.compile(
    r"^\s*(Current [^:]+):\s*([\d.]+)%\s*used"
    r"(?:\s*·\s*resets\s+(.+?)\s*\(([^)]+)\))?\s*$",
    re.M,
)


def parse_claude_reset(text, tzname, now=None):
    """'Oct 29 at 10:39pm' / '10:39pm' / 'Nov 5 at 10am' in tz -> epoch secs."""
    if not text or ZoneInfo is None:
        return None
    try:
        tz = ZoneInfo(tzname)
    except Exception:
        return None
    now_dt = datetime.fromtimestamp(time.time() if now is None else now, tz)
    m = re.match(r"^(?:([A-Z][a-z]{2})\s+(\d{1,2})(?:,\s*(\d{4}))?\s+at\s+)?"
                 r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)$", text.strip(), re.I)
    if not m:
        return None
    mon, day, year, hh, mm, ap = m.groups()
    hour = int(hh) % 12 + (12 if ap.lower() == "pm" else 0)
    minute = int(mm or 0)
    if mon:
        try:
            month = datetime.strptime(mon, "%b").month
        except ValueError:
            return None
        y = int(year) if year else now_dt.year
        dt = datetime(y, month, int(day), hour, minute, tzinfo=tz)
        # No year given: a reset is always in the future, so a date that
        # appears to lie well in the past belongs to next year (Dec -> Jan).
        if not year and dt < now_dt - timedelta(days=1):
            dt = dt.replace(year=y + 1)
    else:
        dt = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if dt < now_dt:
            dt += timedelta(days=1)
    return int(dt.timestamp())


def claude_label(raw):
    s = raw.strip()
    if s == "Current session":
        return "Session (5h)"
    m = re.match(r"Current week \((.+)\)", s)
    if m:
        scope = m.group(1)
        return "Weekly" if scope == "all models" else "Weekly · " + scope
    return s.replace("Current ", "").capitalize()


def parse_claude_usage(text, now=None):
    """Returns (auth_mode, [limit, ...])."""
    now = time.time() if now is None else now
    low = (text or "").lower()
    if "using your subscription" in low:
        mode = "subscription"
    elif "api key" in low or "api usage billing" in low:
        mode = "api_key"
    else:
        mode = "unknown"
    limits = []
    for m in USAGE_LINE.finditer(text or ""):
        raw, pct, reset_txt, tzname = m.groups()
        reset = parse_claude_reset(reset_txt, tzname, now) if reset_txt else None
        lim = make_limit(claude_label(raw), float(pct), reset, "claude /usage", now=now, as_of=now)
        if reset_txt and reset is None and lim["available"]:
            lim["resetText"] = "Resets " + reset_txt  # keep source text if unparsable
        limits.append(lim)
    return mode, limits


# ---------- Codex rate limits (app-server camelCase or session snake_case) ----------
def _g(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d.get(k)
    return None


def parse_codex_rate_limits(rl, source, as_of=None, now=None):
    out = []
    if not isinstance(rl, dict):
        return out
    name = _g(rl, "limitName", "limit_name")
    for slot in ("primary", "secondary"):
        w = rl.get(slot)
        if not isinstance(w, dict):
            continue
        mins = to_num(_g(w, "windowDurationMins", "window_minutes"))
        label = window_label(mins)
        if name:
            label = name + " · " + label
        out.append(make_limit(label, _g(w, "usedPercent", "used_percent"),
                              _g(w, "resetsAt", "resets_at"), source,
                              window_mins=mins, as_of=as_of, now=now))
    return out


PLAN_NAMES = {"free": "Free", "go": "Go", "plus": "Plus", "pro": "Pro",
              "team": "Team", "business": "Business", "enterprise": "Enterprise",
              "edu": "Edu", "max": "Max"}


def plan_name(raw):
    if not raw:
        return None
    return PLAN_NAMES.get(str(raw).lower(), str(raw).replace("_", " ").title())


# ============================================================
#  Binary discovery
# ============================================================
def _newest(paths):
    paths = [p for p in paths if os.path.isfile(p) and os.access(p, os.X_OK)]
    return max(paths, key=os.path.getmtime) if paths else None


def find_bin(name):
    for d in SUBPROC_ENV["PATH"].split(":"):
        p = os.path.join(d, name)
        if d and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    ext = os.path.expanduser("~/.vscode/extensions")
    if name == "claude":
        return _newest([os.path.expanduser("~/.claude/local/claude")]
                       + glob.glob(ext + "/anthropic.claude-code-*/resources/native-binary/claude"))
    if name == "codex":
        return _newest(["/Applications/Codex.app/Contents/Resources/codex"]
                       + glob.glob(ext + "/openai.chatgpt-*/bin/macos-*/codex"))
    return None


def _run_json(argv, timeout=TIMEOUT):
    os.makedirs(WORK_DIR, exist_ok=True)
    res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                         env=SUBPROC_ENV, cwd=WORK_DIR, stdin=subprocess.DEVNULL)
    out = (res.stdout or "").strip()
    i = out.find("{")
    if i < 0:
        # stderr may mention auth problems; never echo it wholesale (could
        # contain tokens) — just a short, generic reason.
        raise RuntimeError("no JSON output (exit %s)" % res.returncode)
    return json.loads(out[i:])


def _short_err(e):
    if isinstance(e, subprocess.TimeoutExpired):
        return "timed out"
    if isinstance(e, FileNotFoundError):
        return "CLI not found"
    return str(e)[:80] or e.__class__.__name__


# ============================================================
#  Claude Code — official
# ============================================================
def claude_official():
    out = {"plan": None, "authMode": "unknown", "account": "", "limits": [], "limitsError": None}
    b = find_bin("claude")
    if not b:
        out["limitsError"] = "Claude Code CLI not found"
        return out
    try:
        st = _run_json([b, "auth", "status", "--json"], timeout=30)
        if not st.get("loggedIn"):
            out["limitsError"] = "Not logged in"
            return out
        out["account"] = mask_email(st.get("email") or "")
        method = st.get("authMethod") or ""
        if method == "claude.ai":
            out["authMode"] = "subscription"
            out["plan"] = plan_name(st.get("subscriptionType")) or "Plan unknown"
        elif method:
            out["authMode"] = "api_key"
            out["plan"] = "API key"
    except Exception as e:
        out["limitsError"] = "auth status: " + _short_err(e)
    if out["authMode"] == "api_key":
        out["limitsError"] = "API-key billing — no subscription limits"
        return out
    try:
        j = _run_json([b, "-p", "/usage", "--output-format", "json",
                       "--no-session-persistence", "--strict-mcp-config"], timeout=60)
        if j.get("num_turns") or j.get("total_cost_usd"):
            raise RuntimeError("unexpected model call")
        mode, limits = parse_claude_usage(j.get("result") or "")
        if mode != "unknown":
            out["authMode"] = mode
        out["limits"] = limits
        if not limits:
            out["limitsError"] = "Usage unavailable"
    except Exception as e:
        out["limitsError"] = "Usage unavailable (" + _short_err(e) + ")"
    return out


# ============================================================
#  Codex — official
# ============================================================
def codex_app_server(timeout=25):
    """Minimal JSON-RPC client for `codex app-server` (stdio, JSONL)."""
    b = find_bin("codex")
    if not b:
        raise FileNotFoundError("codex")
    os.makedirs(WORK_DIR, exist_ok=True)
    p = subprocess.Popen([b, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, env=SUBPROC_ENV, cwd=WORK_DIR)
    results = {}
    deadline = time.time() + timeout
    # A reader thread, not select(): select() can't see lines already sitting
    # in the TextIOWrapper buffer, which hangs when replies arrive together.
    lines = queue.Queue()

    def reader():
        for line in p.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()

    def send(obj):
        p.stdin.write(json.dumps(obj) + "\n")
        p.stdin.flush()

    def wait_for(ids):
        while not all(i in results for i in ids):
            left = deadline - time.time()
            if left <= 0:
                raise subprocess.TimeoutExpired("codex app-server", timeout)
            try:
                line = lines.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("app-server exited")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                results[msg["id"]] = msg

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "ai_usage_widget",
                                        "title": "AI Usage Widget", "version": "2.0"}}})
        wait_for([1])
        send({"jsonrpc": "2.0", "method": "initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "account/read", "params": {}})
        send({"jsonrpc": "2.0", "id": 3, "method": "account/rateLimits/read"})
        wait_for([2, 3])
    finally:
        try:
            p.stdin.close()
        except Exception:
            pass
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            p.kill()
    return results[2], results[3]


def codex_session_snapshot():
    """Newest rate_limits object Codex persisted to its session logs."""
    files = glob.glob(os.path.expanduser("~/.codex/sessions/**/*.jsonl"), recursive=True)
    for f in sorted(files, key=os.path.getmtime, reverse=True)[:5]:
        try:
            with open(f, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            rl = (ev.get("payload") or {}).get("rate_limits")
            if isinstance(rl, dict):
                ts = ev.get("timestamp")
                try:
                    as_of = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    as_of = os.path.getmtime(f)
                return rl, as_of
    return None, None


def codex_auth_mode_from_file():
    try:
        with open(os.path.expanduser("~/.codex/auth.json"), encoding="utf-8") as f:
            d = json.load(f)
        m = (d.get("auth_mode") or "").lower()
        return "subscription" if m == "chatgpt" else ("api_key" if m in ("apikey", "api_key") else "unknown")
    except Exception:
        return "unknown"


def codex_official(now=None):
    now = time.time() if now is None else now
    out = {"plan": None, "authMode": "unknown", "account": "", "limits": [], "limitsError": None}
    try:
        acct_msg, rl_msg = codex_app_server()
        acct = ((acct_msg.get("result") or {}).get("account")) or {}
        t = (acct.get("type") or "").lower()
        if t == "chatgpt":
            out["authMode"] = "subscription"
            out["plan"] = plan_name(acct.get("planType")) or "ChatGPT subscription"
            out["account"] = mask_email(acct.get("email") or "")
        elif t:
            out["authMode"] = "api_key"
            out["plan"] = "API key"
        if out["authMode"] == "api_key":
            out["limitsError"] = "API-key billing — no subscription limits"
            return out
        if "error" in rl_msg:
            raise RuntimeError("rateLimits: " + str((rl_msg["error"] or {}).get("message", ""))[:60])
        res = rl_msg.get("result") or {}
        by_id = res.get("rateLimitsByLimitId") or {}
        main = res.get("rateLimits") or {}
        out["limits"] = parse_codex_rate_limits(main, "codex app-server", as_of=now, now=now)
        for lid, rl in by_id.items():
            if lid != (main.get("limitId") or "codex"):
                out["limits"] += parse_codex_rate_limits(rl, "codex app-server", as_of=now, now=now)
        if not out["plan"]:
            out["plan"] = plan_name(main.get("planType")) or "Plan unknown"
        if not out["limits"]:
            out["limitsError"] = "Usage unavailable"
        return out
    except Exception as e:
        live_err = _short_err(e)
        out["liveError"] = live_err
        try:
            with open(os.path.join(WORK_DIR, "codex-live-error.log"), "a", encoding="utf-8") as fh:
                fh.write("%s %s: %r\n" % (datetime.now().isoformat(timespec="seconds"),
                                          e.__class__.__name__, str(e)[:200]))
        except OSError:
            pass
    # Fallback: last snapshot Codex itself recorded (official, but possibly stale).
    out["authMode"] = codex_auth_mode_from_file()
    if out["authMode"] == "api_key":
        out["plan"] = "API key"
        out["limitsError"] = "API-key billing — no subscription limits"
        return out
    rl, as_of = codex_session_snapshot()
    if rl:
        out["plan"] = plan_name(rl.get("plan_type")) or "ChatGPT subscription"
        out["limits"] = parse_codex_rate_limits(rl, "codex session log", as_of=as_of, now=now)
        out["stale"] = True
    if not any(l["available"] for l in out["limits"]):
        out["limitsError"] = "Usage unavailable (" + live_err + ")"
    out["plan"] = out["plan"] or "ChatGPT subscription"
    return out


# ============================================================
#  Local token estimates (ccusage)
# ============================================================
def summarize_daily(daily, count_cache_reads=True, today=None):
    """ccusage `daily` rows -> rolling token totals + 30-day series.

    Pure function. Token values stay raw integers end-to-end.
    """
    today = today or date.today()
    week_start = today - timedelta(days=6)    # rolling 7 days incl. today
    month_start = today - timedelta(days=29)  # rolling 30 days incl. today
    totals = {"today": 0, "7d": 0, "30d": 0}
    per_day = {}
    for d in daily or []:
        try:
            dt = date.fromisoformat((d.get("date") or "")[:10])
        except ValueError:
            continue
        tokens = int(to_num(d.get("totalTokens", 0)) or 0)
        if not count_cache_reads:
            tokens -= int(to_num(d.get("cacheReadTokens", 0)) or 0)
        models = []
        # Claude rows: modelBreakdowns list; Codex rows: models dict.
        for m in (d.get("modelBreakdowns") or []):
            mtok = sum((to_num(m.get(k, 0)) or 0) for k in
                       ("inputTokens", "outputTokens", "cacheCreationTokens"))
            if count_cache_reads:
                mtok += to_num(m.get("cacheReadTokens", 0)) or 0
            models.append({"name": m.get("modelName") or "?", "tokens": int(mtok)})
        mdict = d.get("models")
        if not models and isinstance(mdict, dict):
            for name, m in mdict.items():
                mtok = to_num(m.get("totalTokens", 0)) or 0
                if not count_cache_reads:
                    mtok -= to_num(m.get("cacheReadTokens", 0)) or 0
                models.append({"name": name, "tokens": int(mtok)})
        models.sort(key=lambda x: x["tokens"], reverse=True)
        rec = per_day.setdefault(dt.isoformat(), {"tokens": 0, "models": []})
        rec["tokens"] += tokens
        rec["models"] += models
        if dt == today:
            totals["today"] += tokens
        if week_start <= dt <= today:
            totals["7d"] += tokens
        if month_start <= dt <= today:
            totals["30d"] += tokens
    series = []
    cur = month_start
    while cur <= today:
        rec = per_day.get(cur.isoformat()) or {"tokens": 0, "models": []}
        series.append({"date": cur.isoformat(), "tokens": rec["tokens"], "models": rec["models"]})
        cur += timedelta(days=1)
    return {"totals": totals, "series": series}


def local_estimate(cfg):
    try:
        j = _run_json(cfg["estimate_command"])
        return summarize_daily(j.get("daily"), cfg.get("count_cache_reads", True))
    except Exception as e:
        return {"error": "Estimate unavailable (" + _short_err(e) + ")"}


# ============================================================
def main():
    now = datetime.now()
    out = {"asOf": now.strftime("%H:%M"), "date": now.strftime("%b %d"),
           "ts": int(now.timestamp())}
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_off = {"claude": ex.submit(claude_official), "chatgpt": ex.submit(codex_official)}
        f_est = {k: ex.submit(local_estimate, cfg) for k, cfg in CONFIG.items()}
        for name, cfg in CONFIG.items():
            try:
                r = f_off[name].result()
            except Exception as e:
                r = {"plan": None, "authMode": "unknown", "account": "", "limits": [],
                     "limitsError": "Usage unavailable (" + _short_err(e) + ")"}
            r["label"] = cfg["label"]
            r["accountUrl"] = cfg["account_url"]
            r["estimate"] = f_est[name].result()
            r["ts"] = int(time.time())
            out[name] = r
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"fatal": str(e)[:200]}, ensure_ascii=False))
