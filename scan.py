#!/usr/bin/env python3
"""Daily open-source opportunity scanner (AI / neuroscience).

1. Re-verify every tracked issue: closed / assigned / has a PR  -> moved to closed.json
2. Search curated (+ weekly discovered) repos for beginner-friendly issues
3. Score, cap, and write data/issues.json + ISSUES.md

Stdlib only. Needs env GITHUB_TOKEN (GitHub Actions provides it).
"""
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
NOW = dt.datetime.now(dt.timezone.utc)
TOKEN = os.environ.get("GITHUB_TOKEN", "")
CALL_BUDGET = 700        # GitHub Actions token allows 1000/hour; stay well under
_calls = 0


def budget_ok():
    return _calls < CALL_BUDGET


# ---------------------------------------------------------------- GitHub API
def gh(path, params=None, accept="application/vnd.github+json"):
    global _calls
    if not budget_ok():
        return None
    _calls += 1
    return _request(path, params, accept)


def _request(path, params, accept):
    url = "https://api.github.com" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Accept": accept, "User-Agent": "oss-tracker",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore").lower()
            limited = e.code == 429 or (e.code == 403 and (
                e.headers.get("X-RateLimit-Remaining") == "0" or "rate limit" in body))
            if limited and attempt < 2:
                reset = int(e.headers.get("X-RateLimit-Reset", 0) or 0)
                wait = max(5, min(90, reset - time.time() + 1)) if reset else 30
                print(f"  rate limited, sleeping {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if limited:  # still limited: stop making calls this run
                global CALL_BUDGET
                CALL_BUDGET = 0
                print("  rate limit persists; stopping API calls for this run", file=sys.stderr)
                return None
            if e.code in (403, 404, 410, 422):
                return None
            raise
        except urllib.error.URLError:
            time.sleep(5)
    return None


def search_issues(q):
    time.sleep(2.2)  # search API: 30 req/min
    res = gh("/search/issues", {"q": q, "per_page": 30, "sort": "created", "order": "desc"})
    return (res or {}).get("items", [])


# ---------------------------------------------------------------- helpers
def load(name, default):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return default


def save(name, obj):
    with open(os.path.join(DATA, name), "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)


def parse_t(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else NOW


def days(s):
    return (NOW - parse_t(s)).days


def key(repo, num):
    return f"{repo}#{num}"


# ---------------------------------------------------------------- repo meta
_meta_cache = {}


def repo_meta(repo):
    if repo not in _meta_cache:
        m = gh(f"/repos/{repo}") or {}
        _meta_cache[repo] = {
            "stars": m.get("stargazers_count", 0),
            "lang": m.get("language") or "",
            "pushed": m.get("pushed_at"),
            "archived": m.get("archived", False),
            "ok": bool(m),
        }
    return _meta_cache[repo]


# ---------------------------------------------------------------- verification
def check_issue(repo, num, cfg):
    """Return (status, reason). status in active | maybe-claimed | yours | closed."""
    me = cfg["github_user"].lower()
    iss = gh(f"/repos/{repo}/issues/{num}")
    if iss is None and not budget_ok():
        return "unchecked", ""
    if not iss:
        return "closed", "not found"
    if iss.get("state") != "open":
        return "closed", "issue closed"
    assignees = [a["login"].lower() for a in iss.get("assignees") or []]
    if me in assignees:
        return "yours", "assigned to you"
    if assignees:
        return "closed", "assigned to " + assignees[0]

    tl = gh(f"/repos/{repo}/issues/{num}/timeline", {"per_page": 100}) or []
    claimed = False
    for ev in tl:
        e = ev.get("event")
        if e == "cross-referenced":
            src = (ev.get("source") or {}).get("issue") or {}
            if src.get("pull_request") and src.get("state") == "open":
                author = (src.get("user") or {}).get("login", "").lower()
                if author == me:
                    return "yours", "your open PR"
                return "closed", "open PR by " + author
        elif e == "commented" and days(ev.get("created_at")) <= 45:
            body = (ev.get("body") or "").lower()
            who = (ev.get("user") or ev.get("actor") or {}).get("login", "").lower()
            if who != me and any(p in body for p in cfg["claim_phrases"]):
                claimed = True
    return ("maybe-claimed", "recent claim comment") if claimed else ("active", "")


# ---------------------------------------------------------------- scoring
def score(it, cfg):
    s = {3: 30, 2: 20, 1: 10}.get(it["tier"], 10)
    s += max([cfg["labels"].get(l.lower(), 0) for l in it["labels"]] + [0])
    age = days(it["created"])
    s += 10 if age < 30 else 5 if age < 90 else -10 if age > 365 else 0
    c = it.get("comments", 0)
    s += 5 if c <= 2 else -10 if c > 8 else 0
    s += min(8, int(math.log10(max(it.get("stars", 1), 1)) * 2))
    if it.get("status") == "maybe-claimed":
        s -= 20
    if it.get("status") == "yours":
        s += 100
    return s


# ---------------------------------------------------------------- discovery
def discover(cfg, known, n_discovered):
    d = cfg["discovery"]
    if n_discovered >= d["max_discovered_total"]:
        return {}
    if NOW.weekday() != d["weekday"] and os.environ.get("FORCE_DISCOVERY") != "1":
        return {}
    since = (NOW - dt.timedelta(days=60)).date().isoformat()
    found = {}
    for topic, tier in d["topics"].items():
        stars = d["big_topic_min_stars"] if topic in d["big_topics"] else d["min_stars"]
        time.sleep(2.2)
        res = gh("/search/repositories", {
            "q": f"topic:{topic} stars:>={stars} pushed:>={since} good-first-issues:>0 archived:false",
            "sort": "stars", "per_page": 15}) or {}
        for r in res.get("items", []):
            name = r["full_name"]
            if name not in known and name not in found:
                found[name] = tier
        if len(found) >= d["max_new_repos_per_run"]:
            break
    return dict(list(found.items())[: d["max_new_repos_per_run"]])


# ---------------------------------------------------------------- main
def main():
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    lim = cfg["limits"]
    os.makedirs(DATA, exist_ok=True)
    issues = load("issues.json", {})
    closed = load("closed.json", {})
    discovered = load("repos_discovered.json", {})

    repos = {}
    for tier, names in cfg["repos"].items():
        for n in names:
            repos[n] = int(tier)
    for n, t in discovered.items():
        repos.setdefault(n, int(t))

    new_repos = discover(cfg, repos, len(discovered))
    if new_repos:
        print(f"discovered {len(new_repos)} repos: {', '.join(new_repos)}")
        discovered.update(new_repos)
        repos.update(new_repos)

    # 1. verify tracked issues
    closed_today = []
    for k, it in list(issues.items()):
        if not budget_ok():
            break
        status, why = check_issue(it["repo"], it["number"], cfg)
        if status == "unchecked":
            continue
        if status == "closed":
            it.update(status="closed", closed_reason=why, closed_on=NOW.date().isoformat())
            closed[k] = issues.pop(k)
            closed_today.append(k)
        else:
            it.update(status=status, note=why, checked=NOW.date().isoformat())
    print(f"verified {len(issues) + len(closed_today)}, closed {len(closed_today)}")

    # 2. search for new ones
    label_q = ",".join(f'"{l}"' for l in cfg["search_labels"])
    candidates = []
    for repo, tier in repos.items():
        if not budget_ok():
            break
        m = repo_meta(repo)
        if not m["ok"] or m["archived"] or days(m["pushed"]) > lim["skip_repo_if_no_push_days"]:
            continue
        q = f"repo:{repo} is:issue is:open no:assignee label:{label_q}"
        for r in search_issues(q):
            k = key(repo, r["number"])
            if k in issues or k in closed or days(r["created_at"]) > lim["skip_issue_older_than_days"]:
                continue
            candidates.append({
                "repo": repo, "number": r["number"], "title": r["title"], "url": r["html_url"],
                "labels": [l["name"] for l in r.get("labels", [])], "created": r["created_at"],
                "comments": r.get("comments", 0), "tier": tier, "stars": m["stars"],
                "lang": m["lang"], "added": NOW.date().isoformat(), "status": "active",
            })

    for c in candidates:
        c["score"] = score(c, cfg)
    candidates.sort(key=lambda c: -c["score"])

    per_repo = {}
    for it in issues.values():
        per_repo[it["repo"]] = per_repo.get(it["repo"], 0) + 1
    added, checks = [], 0
    for c in candidates:
        if checks >= 2 * lim["max_new_per_run"] or not budget_ok():
            break
        if len(added) >= lim["max_new_per_run"] or len(issues) >= lim["max_active"]:
            break
        if per_repo.get(c["repo"], 0) >= lim["max_per_repo"]:
            continue
        # verify before adding (catches open PRs / claim comments)
        checks += 1
        status, why = check_issue(c["repo"], c["number"], cfg)
        if status in ("closed", "unchecked"):
            continue
        c.update(status=status, note=why, checked=NOW.date().isoformat())
        k = key(c["repo"], c["number"])
        issues[k] = c
        per_repo[c["repo"]] = per_repo.get(c["repo"], 0) + 1
        added.append(k)
    print(f"added {len(added)} new · {_calls} API calls used")

    for it in issues.values():
        it["score"] = score(it, cfg)

    # keep closed archive bounded
    if len(closed) > 1000:
        closed = dict(sorted(closed.items(), key=lambda kv: kv[1].get("closed_on", ""))[-1000:])

    save("issues.json", issues)
    save("closed.json", closed)
    save("repos_discovered.json", discovered)
    write_md(issues, closed, closed_today, added, lim["top_n_for_audit"])


TIER_NAME = {3: "Neuro / neuro-AI", 2: "AI / ML / LLM", 1: "General (easy merges)"}


def row(it):
    labels = ", ".join(l for l in it["labels"][:3])
    title = it["title"].replace("|", "/")[:90]
    flag = {"maybe-claimed": " ⚠️ claimed?", "yours": " ⭐ yours"}.get(it["status"], "")
    return (f"| {it['score']} | [{title}]({it['url']}){flag} | {it['repo']} | {it['lang']} "
            f"| {labels} | {days(it['created'])}d | {it.get('comments', 0)} |")


HDR = "| Score | Issue | Repo | Lang | Labels | Age | 💬 |\n|---|---|---|---|---|---|---|"


def write_md(issues, closed, closed_today, added, top_n):
    items = sorted(issues.values(), key=lambda i: -i["score"])
    out = [
        "# Open-source opportunities: AI × Neuroscience",
        f"_Updated {NOW.strftime('%Y-%m-%d %H:%M UTC')} · {len(items)} active · "
        f"+{len(added)} new today · {len(closed_today)} closed today_",
        "",
        f"## Top {top_n} candidates",
        "<!-- TOP-START -->", HDR, *[row(i) for i in items[:top_n]], "<!-- TOP-END -->", "",
    ]
    for t in (3, 2, 1):
        group = [i for i in items if i["tier"] == t]
        if group:
            out += [f"## {TIER_NAME[t]} ({len(group)})", HDR, *[row(i) for i in group], ""]
    recent = sorted(closed.values(), key=lambda i: i.get("closed_on", ""), reverse=True)[:15]
    if recent:
        out += ["## Recently closed", "| Issue | Repo | Why | On |", "|---|---|---|---|"]
        out += [f"| [{i['title'][:70].replace('|','/')}]({i['url']}) | {i['repo']} "
                f"| {i.get('closed_reason','')} | {i.get('closed_on','')} |" for i in recent]
    with open(os.path.join(ROOT, "ISSUES.md"), "w") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
