# scripts/

Tooling for the "How It Works" walkthrough video shown in the landing page modal.
Not imported by the app — these are run by hand.

## Re-recording the video

The video is a real screen recording of this app, driven by Playwright. Re-record it
whenever the UI it shows changes (sign-in, dashboard, add-expense form, expenses list,
or the Dex ask bar).

```bash
# 1. Seed the throwaway demo account (idempotent)
.venv/bin/python scripts/seed_demo.py

# 2. Start the app in another shell
.venv/bin/python app.py

# 3. Record + encode
.venv/bin/python scripts/record_demo.py
```

Outputs, committed to the repo and served by Flask:

- `static/video/how-it-works.mp4` — H.264, what virtually every browser plays
- `static/video/how-it-works.webm` — VP9 fallback
- `static/video/how-it-works-poster.jpg` — poster frame

Raw clips land in a scratch directory (override with `DEMO_WORK_DIR`), not the repo.

### Useful flags

```bash
.venv/bin/python scripts/record_demo.py --segment core   # re-shoot the core loop only
.venv/bin/python scripts/record_demo.py --segment dex    # re-shoot the Dex answer only
.venv/bin/python scripts/record_demo.py --segment none   # re-encode existing clips
.venv/bin/python scripts/record_demo.py --headed         # watch it happen
```

The video is recorded in two segments and concatenated, so a bad take of one doesn't
cost you the other. The Dex clip's sign-in is timed and trimmed off during encoding,
since the core clip already showed it.

## Things that will bite you

- **Never record the real account.** `scripts/seed_demo.py` exists so no personal
  spending appears on a public landing page. It writes `demo@balancedesk.local` into
  `instance/balance_desk.db` (gitignored), which also shows up in the admin panel's
  user list — harmless, delete it if it bothers you.
- **Dex costs Groq tokens** (~11k per take, against a 100k/day cap) and Groq
  intermittently returns `tool_use_failed`, which the app renders as "Dex is taking a
  quick break". The script detects that fallback and retries up to 3 times; if all
  takes fail it still produces the core video and tells you to re-run `--segment dex`.
- **Playwright's bundled ffmpeg is not enough.** It's a `--disable-everything` build
  (VP8/webm only — no H.264, no concat filter), so encoding uses the full binary from
  the `imageio-ffmpeg` dev dependency: `.venv/bin/pip install imageio-ffmpeg`. It is
  deliberately not in `requirements.txt` — production never encodes video.
- **Playwright draws no cursor in recordings.** The script injects a synthetic cursor
  and a caption bar into every page; without them the footage looks unnervingly
  autonomous. Both live in `OVERLAY_JS` in `record_demo.py`.
- **The dashboard coach tour** (`templates/dashboard.html`, gated on
  `db_tour_seen_<user id>`) covers the stats on a first visit. The script pre-sets that
  key. If the key name changes, the tour will reappear in the footage.
- **Avoid the stub tabs.** Analysis, Messages, and Calculate currently render
  placeholder pages; the script deliberately never opens them.
