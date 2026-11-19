# oss-tracker — AI × Neuroscience open-source opportunities

Auto-maintained list of beginner-friendly issues in neuroimaging, computational
neuroscience, deep learning, ML and LLM projects. See **[ISSUES.md](ISSUES.md)**.

## How it works
- **Daily (06:00 IST), GitHub Actions, 0 Claude tokens** — `scan.py`:
  1. re-checks every tracked issue → moves it to `data/closed.json` if it was
     closed, assigned to someone, or got an open PR; flags "can I work on this?" comments as ⚠️
  2. searches ~65 curated repos (+ repos auto-discovered by topic every Monday)
     for `good first issue` / `beginner` / `easy` issues
  3. scores (neuro tier > AI tier > general; fresher, quieter issues rank higher) and
     rewrites `ISSUES.md`
- **Weekly (Sunday), Claude** — reads the top 15 and picks the best 5 to start with.

Issues assigned to you (or with your open PR) stay on the list marked ⭐ yours.

## Tuning (edit `config.json`)
| Want to… | Change |
|---|---|
| add / remove a repo | `repos` → tier `"3"` neuro, `"2"` AI/ML, `"1"` general |
| more / fewer issues | `limits.max_active`, `max_new_per_run`, `max_per_repo` |
| harder issues later | add `"help wanted"` to `search_labels` |
| new research areas | `discovery.topics` |

Run manually: **Actions → daily-oss-scan → Run workflow** (tick *force discovery* to find new repos now).
Test offline: `python tests/test_scan.py`.
