"""Record the "How It Works" screencast by driving the real app in Chromium.

Playwright records the viewport to .webm; its bundled ffmpeg stitches the
segments into the mp4/webm/poster served from static/video/.

Prerequisites: the app running on http://127.0.0.1:5001 and the demo account
seeded (scripts/seed_demo.py).

    .venv/bin/python scripts/record_demo.py                 # everything
    .venv/bin/python scripts/record_demo.py --segment dex   # re-shoot Dex only
    .venv/bin/python scripts/record_demo.py --segment none  # re-encode only

Playwright draws no mouse pointer in recorded video, so a synthetic cursor and
a caption bar are injected into every page; without them the footage looks like
a ghost is using the app.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT      = Path(__file__).resolve().parent.parent
OUT_DIR   = ROOT / 'static' / 'video'
WORK_DIR  = Path(os.environ.get(
    'DEMO_WORK_DIR',
    '/private/tmp/claude-501/-Users-chaitya-Documents-Project-expense-tracker/'
    'b13269f6-9ad2-4c8d-b5e9-9e9ca0f549b8/scratchpad/demo-video'))

BASE_URL  = os.environ.get('DEMO_BASE_URL', 'http://127.0.0.1:5001')
EMAIL     = 'demo@balancedesk.local'
PASSWORD  = 'DemoDesk!2026'

WIDTH, HEIGHT = 1280, 720

def _ffmpeg():
    """Playwright's bundled ffmpeg is a --disable-everything build (VP8/webm
    only, no libx264, no concat filter), so use the full binary that ships with
    the imageio-ffmpeg dev dependency instead."""
    try:
        import imageio_ffmpeg
    except ImportError:
        raise SystemExit('missing dev dependency: .venv/bin/pip install imageio-ffmpeg')
    return imageio_ffmpeg.get_ffmpeg_exe()


# ------------------------------------------------------------------ #
# Injected overlays: synthetic cursor + caption bar                    #
# ------------------------------------------------------------------ #

OVERLAY_JS = r"""
(() => {
  if (window.__demoOverlay) return;
  window.__demoOverlay = true;

  const install = () => {
    if (!document.body || document.getElementById('__demo_cursor')) return;

    const style = document.createElement('style');
    style.textContent = `
      #__demo_cursor {
        position: fixed; left: 0; top: 0; width: 22px; height: 22px;
        z-index: 2147483647; pointer-events: none;
        transform: translate(-2px, -2px);
        transition: none;
        filter: drop-shadow(0 2px 4px rgba(0,0,0,.35));
      }
      #__demo_pulse {
        position: fixed; left: 0; top: 0; width: 34px; height: 34px;
        margin: -17px 0 0 -17px; border-radius: 50%;
        border: 2px solid #1a472a; background: rgba(26,71,42,.18);
        z-index: 2147483646; pointer-events: none; opacity: 0;
        transform: scale(.3);
      }
      #__demo_pulse.go { animation: __demo_ping .45s ease-out; }
      @keyframes __demo_ping {
        0%   { opacity: .9; transform: scale(.3); }
        100% { opacity: 0;  transform: scale(1.25); }
      }
      #__demo_caption {
        position: fixed; left: 50%; bottom: 34px; transform: translateX(-50%) translateY(8px);
        z-index: 2147483645; pointer-events: none;
        background: #1a472a; color: #fff;
        font: 600 17px/1.35 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        letter-spacing: .2px;
        padding: 12px 22px; border-radius: 999px;
        box-shadow: 0 10px 30px rgba(0,0,0,.28);
        opacity: 0; transition: opacity .32s ease, transform .32s ease;
        max-width: 78vw; text-align: center;
      }
      #__demo_caption.show { opacity: 1; transform: translateX(-50%) translateY(0); }
    `;
    document.head.appendChild(style);

    const cur = document.createElement('div');
    cur.id = '__demo_cursor';
    cur.innerHTML =
      '<svg viewBox="0 0 22 22" width="22" height="22">' +
      '<path d="M3 2l14 7.2-6.1 1.5L8.4 17 3 2z" fill="#fff" stroke="#1a1a1a" ' +
      'stroke-width="1.3" stroke-linejoin="round"/></svg>';
    document.body.appendChild(cur);

    const pulse = document.createElement('div');
    pulse.id = '__demo_pulse';
    document.body.appendChild(pulse);

    const cap = document.createElement('div');
    cap.id = '__demo_caption';
    document.body.appendChild(cap);

    window.__pos = window.__pos || { x: 640, y: 380 };
    cur.style.left = window.__pos.x + 'px';
    cur.style.top  = window.__pos.y + 'px';
  };

  install();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install);
  }

  // Tween the cursor from where it is to (x, y).
  window.__moveTo = (x, y, ms) => new Promise(resolve => {
    install();
    const cur = document.getElementById('__demo_cursor');
    if (!cur) return resolve();
    const from = window.__pos || { x: 640, y: 380 };
    const start = performance.now();
    const step = now => {
      const p = Math.min((now - start) / ms, 1);
      const e = p < .5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;  // ease-in-out
      const cx = from.x + (x - from.x) * e;
      const cy = from.y + (y - from.y) * e;
      cur.style.left = cx + 'px';
      cur.style.top  = cy + 'px';
      if (p < 1) requestAnimationFrame(step);
      else { window.__pos = { x, y }; resolve(); }
    };
    requestAnimationFrame(step);
  });

  window.__clickFx = () => {
    const p = document.getElementById('__demo_pulse');
    const pos = window.__pos || { x: 0, y: 0 };
    if (!p) return;
    p.style.left = pos.x + 'px';
    p.style.top  = pos.y + 'px';
    p.classList.remove('go');
    void p.offsetWidth;
    p.classList.add('go');
  };

  window.__caption = text => {
    install();
    const c = document.getElementById('__demo_caption');
    if (!c) return;
    if (!text) { c.classList.remove('show'); return; }
    c.textContent = text;
    c.classList.add('show');
  };
})();
"""

# Predictable theme, and suppress the one-time dashboard coach tour
# (templates/dashboard.html gates it on db_tour_seen_<user id>) so it doesn't
# cover the stats on camera.
THEME_JS = """
try {
  localStorage.setItem('theme', 'light');
  for (let i = 1; i <= 60; i++) localStorage.setItem('db_tour_seen_' + i, '1');
} catch (e) {}
"""


class Director:
    """Thin wrapper that keeps cursor, captions and real actions in step."""

    def __init__(self, page):
        self.page = page

    def caption(self, text, hold=0.0):
        self.page.evaluate('t => window.__caption(t)', text)
        if hold:
            self.beat(hold)

    def clear_caption(self):
        self.page.evaluate('() => window.__caption("")')

    def beat(self, seconds=0.6):
        self.page.wait_for_timeout(int(seconds * 1000))

    def _center(self, selector):
        el = self.page.wait_for_selector(selector, state='visible', timeout=15000)
        el.scroll_into_view_if_needed()
        self.page.wait_for_timeout(250)
        box = el.bounding_box()
        if not box:
            raise RuntimeError(f'no bounding box for {selector}')
        return el, box['x'] + box['width'] / 2, box['y'] + box['height'] / 2

    def move(self, selector, ms=650):
        el, x, y = self._center(selector)
        self.page.evaluate('([x, y, ms]) => window.__moveTo(x, y, ms)', [x, y, ms])
        self.page.wait_for_timeout(ms + 80)
        return el

    def click(self, selector, ms=650, settle=0.45):
        el = self.move(selector, ms)
        self.page.evaluate('() => window.__clickFx()')
        self.page.wait_for_timeout(160)
        el.click()
        self.beat(settle)

    def type_into(self, selector, text, delay=55):
        el = self.move(selector)
        self.page.evaluate('() => window.__clickFx()')
        self.page.wait_for_timeout(140)
        el.click()
        el.type(text, delay=delay)
        self.beat(0.35)

    def reinstall(self):
        """Re-inject after a navigation, then restore the caption slot."""
        self.page.evaluate(OVERLAY_JS)


# ------------------------------------------------------------------ #
# Segments                                                            #
# ------------------------------------------------------------------ #

def _new_context(browser, name):
    ctx = browser.new_context(
        viewport={'width': WIDTH, 'height': HEIGHT},
        record_video_dir=str(WORK_DIR / name),
        record_video_size={'width': WIDTH, 'height': HEIGHT},
        device_scale_factor=1,
    )
    ctx.add_init_script(THEME_JS)
    ctx.add_init_script(OVERLAY_JS)
    return ctx


def _sign_in(d):
    page = d.page
    page.goto(f'{BASE_URL}/login', wait_until='networkidle')
    d.reinstall()
    d.beat(0.8)
    d.caption('Sign in to your desk')
    d.type_into('#email', EMAIL)
    d.type_into('#password', PASSWORD)
    d.beat(0.3)
    d.click('button[type="submit"]', settle=0.2)
    page.wait_for_url('**/dashboard', timeout=20000)
    page.wait_for_load_state('networkidle')
    d.reinstall()


def record_core(browser):
    ctx  = _new_context(browser, 'core')
    page = ctx.new_page()
    d    = Director(page)

    _sign_in(d)

    # --- Overview: let the count-up animation and charts play ---
    d.caption('Your month at a glance')
    d.beat(4.2)
    page.mouse.wheel(0, 320)
    d.beat(1.6)
    page.mouse.wheel(0, -320)
    d.beat(0.8)

    # --- Add an expense ---
    d.caption('Log an expense in seconds')
    page.goto(f'{BASE_URL}/add-expense', wait_until='networkidle')
    d.reinstall()
    d.caption('Log an expense in seconds')
    d.beat(0.7)
    # Nudge the form fully into frame so later fields don't yank the page
    # down to the footer mid-take.
    page.mouse.wheel(0, 150)
    d.beat(0.5)

    d.type_into('#title', 'Dinner with friends')
    d.type_into('#amount', '92.40')

    d.click('#cat-cselect .cselect-trigger')
    d.beat(0.5)
    d.click('#cat-cselect-list li[data-value="Eating Out"]')

    # --- Split it ---
    d.caption('Split it with a friend')
    d.beat(0.5)
    d.type_into('#split', '46.20')
    d.beat(1.1)

    d.caption('')
    d.click('button[name="next"][value="list"]', settle=0.2)
    page.wait_for_url('**/expenses**', timeout=20000)
    page.wait_for_load_state('networkidle')
    d.reinstall()

    # --- The list, with the new row on top ---
    d.caption('Every expense, and who owes what')
    d.beat(3.4)
    page.mouse.wheel(0, 380)
    d.beat(2.4)
    d.clear_caption()
    d.beat(0.8)

    path = page.video.path()
    ctx.close()
    return Path(path)


def record_dex(browser, attempts=3):
    """Dex needs a live Groq call; retry a few times before giving up."""
    last_error = None

    for attempt in range(1, attempts + 1):
        ctx  = _new_context(browser, f'dex{attempt}')
        page = ctx.new_page()
        d    = Director(page)
        try:
            # Recording starts the moment the page exists, but this segment is
            # spliced after the core clip — which already showed the sign-in.
            # Time the login so the encoder can trim it off the front.
            t_page = time.monotonic()
            _sign_in(d)
            d.beat(0.8)
            trim = max(time.monotonic() - t_page - 0.4, 0)

            # The dashboard swaps the Dex FAB for the inline ask bar, which
            # forwards into the same modal via window.dexAskAndSend()
            # (templates/dashboard.html:200-219) — better framing on camera.
            d.caption('Ask Dex anything about your spending')
            d.beat(0.6)
            d.type_into('#dexTopInput',
                        'How much did I spend on eating out this month?', delay=42)
            d.beat(0.4)
            d.click('#dexTopSend', settle=0.6)

            # Wait for a real answer: a second AI bubble beyond the greeting.
            page.wait_for_function(
                """() => {
                    const b = document.querySelectorAll('#dexMessages .dex-msg--ai .dex-msg-bubble');
                    return b.length >= 2 && b[b.length - 1].textContent.trim().length > 25;
                }""",
                timeout=45000)
            d.beat(0.6)

            text = page.evaluate(
                """() => {
                    const b = document.querySelectorAll('#dexMessages .dex-msg--ai .dex-msg-bubble');
                    return b[b.length - 1].textContent.trim();
                }""")
            # routes/dex.py:339 swaps any upstream failure (notably Groq's
            # intermittent tool_use_failed) for a friendly fallback, so match
            # on that too — it renders as a normal bubble, not an error.
            BAD = ('taking a quick break', 'error', 'sorry, something went wrong',
                   'not configured', 'failed', 'try again')
            if any(s in text.lower() for s in BAD):
                raise RuntimeError(f'Dex returned a fallback/error bubble: {text[:120]}')

            print(f'  Dex replied: {text[:110]}...')
            d.beat(3.6)
            d.clear_caption()
            d.beat(0.8)

            path = page.video.path()
            ctx.close()
            return Path(path), trim

        except (PWTimeout, RuntimeError) as exc:
            last_error = exc
            print(f'  Dex take {attempt}/{attempts} failed: {exc}')
            ctx.close()
            if attempt < attempts:
                time.sleep(3)

    raise RuntimeError(f'Dex segment failed after {attempts} takes: {last_error}')


# ------------------------------------------------------------------ #
# Encoding                                                            #
# ------------------------------------------------------------------ #

def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-2500:])
        raise SystemExit(f'ffmpeg failed: {" ".join(str(c) for c in cmd[:4])}...')
    return proc


def encode(clips):
    ffmpeg = _ffmpeg()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mp4    = OUT_DIR / 'how-it-works.mp4'
    webm   = OUT_DIR / 'how-it-works.webm'
    poster = OUT_DIR / 'how-it-works-poster.jpg'

    # Separate encodes can't be stream-copied together; concat via filter graph.
    # Each clip may carry a lead-in trim (see record_dex).
    inputs = []
    for clip, trim in clips:
        if trim:
            inputs += ['-ss', f'{trim:.2f}']
        inputs += ['-i', str(clip)]
    n = len(clips)
    graph = ''.join(f'[{i}:v]scale={WIDTH}:{HEIGHT},setsar=1,fps=30[v{i}];' for i in range(n))
    graph += ''.join(f'[v{i}]' for i in range(n)) + f'concat=n={n}:v=1:a=0[out]'

    _run([ffmpeg, '-y', *inputs, '-filter_complex', graph, '-map', '[out]',
          '-c:v', 'libx264', '-profile:v', 'high', '-crf', '25', '-preset', 'slow',
          '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', str(mp4)])

    _run([ffmpeg, '-y', '-i', str(mp4), '-c:v', 'libvpx-vp9', '-crf', '36',
          '-b:v', '0', '-row-mt', '1', '-an', str(webm)])

    _run([ffmpeg, '-y', '-ss', '3', '-i', str(mp4), '-frames:v', '1',
          '-q:v', '3', str(poster)])

    probe = subprocess.run(
        [ffmpeg, '-i', str(mp4)], capture_output=True, text=True).stderr
    duration = next((l.strip() for l in probe.splitlines() if 'Duration' in l), '?')

    print('\nEncoded:')
    for f in (mp4, webm, poster):
        print(f'  {f.relative_to(ROOT)}  {f.stat().st_size / 1e6:.2f} MB')
    print(f'  {duration}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--segment', choices=('all', 'core', 'dex', 'none'), default='all',
                    help='which segment to (re)record; "none" re-encodes existing clips')
    ap.add_argument('--headed', action='store_true', help='watch the run in a real window')
    args = ap.parse_args()

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    core_keep = WORK_DIR / 'core.webm'
    dex_keep  = WORK_DIR / 'dex.webm'
    dex_trim  = WORK_DIR / 'dex.trim'   # seconds of sign-in to cut off the front

    if args.segment != 'none':
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            try:
                if args.segment in ('all', 'core'):
                    print('Recording core loop...')
                    shutil.move(str(record_core(browser)), core_keep)
                    print(f'  {core_keep.name}  {core_keep.stat().st_size / 1e6:.2f} MB')

                if args.segment in ('all', 'dex'):
                    print('Recording Dex...')
                    try:
                        clip, trim = record_dex(browser)
                        shutil.move(str(clip), dex_keep)
                        dex_trim.write_text(f'{trim:.2f}')
                        print(f'  {dex_keep.name}  {dex_keep.stat().st_size / 1e6:.2f} MB '
                              f'(trimming {trim:.1f}s of sign-in)')
                    except RuntimeError as exc:
                        print(f'\n!! {exc}')
                        print('!! Falling back to the core segment alone. '
                              'Re-run with --segment dex to add it later.')
            finally:
                browser.close()

    clips = []
    if core_keep.exists():
        clips.append((core_keep, 0.0))
    if dex_keep.exists():
        trim = float(dex_trim.read_text()) if dex_trim.exists() else 0.0
        clips.append((dex_keep, trim))
    if not clips:
        raise SystemExit('no clips to encode')
    print(f'\nEncoding {len(clips)} clip(s): {", ".join(c.name for c, _ in clips)}')
    encode(clips)


if __name__ == '__main__':
    main()
