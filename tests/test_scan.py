"""Offline test: fakes the GitHub API and runs scan.main() twice."""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import scan  # noqa: E402

now = dt.datetime.now(dt.timezone.utc)
iso = lambda d: (now - dt.timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")

STATE = {"closed": set(), "assigned": {}, "pr": set(), "claim": set()}


def fake_gh(path, params=None, accept=None):
    if path.startswith("/search/repositories"):
        return {"items": [{"full_name": "newlab/neurotool"}]}
    if path.startswith("/search/issues"):
        repo = params["q"].split()[0][5:]
        if repo not in ("nilearn/nilearn", "huggingface/transformers", "newlab/neurotool"):
            return {"items": []}
        return {"items": [{
            "number": n, "title": f"{repo} issue {n} | pipe", "html_url": f"https://github.com/{repo}/issues/{n}",
            "labels": [{"name": "good first issue"}], "created_at": iso(n * 10), "comments": n % 4,
        } for n in range(1, 8)]}
    if path.endswith("/timeline"):
        k = path.split("/repos/")[1].replace("/issues/", "#").replace("/timeline", "")
        ev = []
        if k in STATE["pr"]:
            ev.append({"event": "cross-referenced", "source": {"issue": {
                "pull_request": {"url": "x"}, "state": "open", "user": {"login": "someone"}}}})
        if k in STATE["claim"]:
            ev.append({"event": "commented", "created_at": iso(1), "body": "Hi, can I work on this?",
                       "user": {"login": "bob"}})
        return ev
    if "/issues/" in path:
        k = path.split("/repos/")[1].replace("/issues/", "#")
        a = STATE["assigned"].get(k)
        return {"state": "closed" if k in STATE["closed"] else "open",
                "assignees": [{"login": a}] if a else []}
    if path.startswith("/repos/"):
        return {"stargazers_count": 5000, "language": "Python", "pushed_at": iso(2), "archived": False}
    raise AssertionError(path)


scan._request = lambda path, params=None, accept=None: fake_gh(path, params)
scan.time.sleep = lambda s: None
os.environ["FORCE_DISCOVERY"] = "1"

tmp = tempfile.mkdtemp()
shutil.copy(os.path.join(os.path.dirname(HERE), "config.json"), tmp)
scan.ROOT, scan.DATA = tmp, os.path.join(tmp, "data")

STATE["pr"].add("nilearn/nilearn#2")
STATE["claim"].add("nilearn/nilearn#3")
scan.main()
issues = json.load(open(os.path.join(tmp, "data/issues.json")))
assert "nilearn/nilearn#2" not in issues, "open PR should block adding"
assert issues["nilearn/nilearn#3"]["status"] == "maybe-claimed"
assert sum(1 for k in issues if k.startswith("nilearn/")) == 5, "max_per_repo=5"
assert any(k.startswith("newlab/neurotool") for k in issues), "discovery"
print("run 1:", len(issues), "issues")

# day 2: one closed, one assigned to someone, one assigned to me
STATE["closed"].add("nilearn/nilearn#1")
STATE["assigned"]["huggingface/transformers#1"] = "alice"
STATE["assigned"]["huggingface/transformers#2"] = "PManiAwasthi"
scan.main()
issues = json.load(open(os.path.join(tmp, "data/issues.json")))
closed = json.load(open(os.path.join(tmp, "data/closed.json")))
assert "nilearn/nilearn#1" in closed and "huggingface/transformers#1" in closed
assert issues["huggingface/transformers#2"]["status"] == "yours"
assert "nilearn/nilearn#2" not in issues  # still has PR
md = open(os.path.join(tmp, "ISSUES.md")).read()
assert "Recently closed" in md and "⭐ yours" in md and "<!-- TOP-START -->" in md
print("run 2:", len(issues), "active,", len(closed), "closed")
print(md[:1500])
print("ALL TESTS PASSED")
