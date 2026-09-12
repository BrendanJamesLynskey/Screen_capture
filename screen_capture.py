#!/usr/bin/env python3
"""
Screen capture loop: screenshot -> save -> press Down arrow -> repeat.

Designed for GNOME on Wayland, where xdotool/scrot-style tools cannot see or
drive other windows. Instead it talks to xdg-desktop-portal over D-Bus:

  * org.freedesktop.portal.Screenshot     - full-screen captures
  * org.freedesktop.portal.RemoteDesktop  - synthetic keyboard input

GNOME asks for permission once per run (the "Remote Desktop" dialog) and, on
the very first run, once for screenshots. Both prompts appear *before* the
start-up countdown, so you can approve them and then click into the browser.

Every capture is written to a timestamped subfolder alongside a
slideshow.html viewer that is refreshed after each frame.

Usage:
    ./screen_capture.py                 # 300 captures, 5 s start delay
    ./screen_capture.py -n 50 -s 1.0    # 50 captures, 1 s settle after key
    ./screen_capture.py --slideshow-only captures/20260912-101500
"""

import argparse
import html
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

XK_DOWN = 0xFF54
DEVICE_KEYBOARD = 1

SCRIPT_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Portal plumbing
# ---------------------------------------------------------------------------

class PortalError(RuntimeError):
    pass


class Portal:
    def __init__(self):
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        self.obj = self.bus.get_object(PORTAL_BUS, PORTAL_PATH)
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self._counter = 0

    def _token(self, prefix):
        self._counter += 1
        return f"{prefix}_{os.getpid()}_{self._counter}"

    def request(self, iface, method, *args, options=None, timeout=120):
        """Call a portal method that answers via a Request.Response signal."""
        options = dict(options or {})
        token = self._token("req")
        options["handle_token"] = token
        path = f"{PORTAL_PATH}/request/{self.sender}/{token}"

        loop = GLib.MainLoop()
        result = {}

        def on_response(code, results):
            result["code"] = int(code)
            result["results"] = results
            loop.quit()

        # Subscribe before calling so a fast response cannot be missed.
        match = self.bus.add_signal_receiver(
            on_response, signal_name="Response",
            dbus_interface=REQUEST_IFACE, path=path)
        getattr(dbus.Interface(self.obj, iface), method)(*args, options)
        timer = GLib.timeout_add_seconds(timeout, loop.quit)
        loop.run()
        GLib.source_remove(timer)
        match.remove()

        if "code" not in result:
            raise PortalError(f"{method}: no response within {timeout} s")
        if result["code"] == 1:
            raise PortalError(f"{method}: cancelled by user")
        if result["code"] != 0:
            raise PortalError(f"{method}: failed (response code {result['code']})")
        return result["results"]


class Keyboard:
    """A RemoteDesktop portal session limited to keyboard input."""

    IFACE = "org.freedesktop.portal.RemoteDesktop"

    def __init__(self, portal):
        self.portal = portal
        self.session = None

    def start(self):
        p = self.portal
        res = p.request(self.IFACE, "CreateSession",
                        options={"session_handle_token": p._token("sess")})
        self.session = dbus.ObjectPath(res["session_handle"])
        p.request(self.IFACE, "SelectDevices", self.session,
                  options={"types": dbus.UInt32(DEVICE_KEYBOARD)})
        res = p.request(self.IFACE, "Start", self.session, "", timeout=300)
        if not int(res.get("devices", 0)) & DEVICE_KEYBOARD:
            raise PortalError("Remote Desktop session granted without keyboard access")

    def tap(self, keysym):
        iface = dbus.Interface(self.portal.obj, self.IFACE)
        for state in (1, 0):  # press, release
            iface.NotifyKeyboardKeysym(self.session, {}, dbus.Int32(keysym),
                                       dbus.UInt32(state))
            time.sleep(0.03)

    def close(self):
        if self.session is None:
            return
        try:
            obj = self.portal.bus.get_object(PORTAL_BUS, self.session)
            dbus.Interface(obj, SESSION_IFACE).Close()
        except dbus.DBusException:
            pass
        self.session = None


def take_screenshot(portal, dest):
    """Capture the whole screen and move the portal's file to `dest`."""
    res = portal.request("org.freedesktop.portal.Screenshot", "Screenshot", "",
                         options={"interactive": False})
    src = Path(unquote(urlparse(str(res["uri"])).path))
    shutil.move(str(src), dest)


# ---------------------------------------------------------------------------
# Slideshow
# ---------------------------------------------------------------------------

SLIDESHOW_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#111; --fg:#eee; --muted:#888; --accent:#4da3ff; --bar:#1c1c1c; }
  * { box-sizing:border-box; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--fg);
    font:14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
  body { display:flex; flex-direction:column; }
  #stage { flex:1; min-height:0; display:flex; align-items:center; justify-content:center;
    cursor:pointer; }
  #stage img { max-width:100%; max-height:100%; object-fit:contain; display:block; }
  #bar { display:flex; gap:10px; align-items:center; padding:8px 12px; background:var(--bar);
    flex-wrap:wrap; }
  button, select { background:#2a2a2a; color:var(--fg); border:1px solid #3a3a3a;
    border-radius:4px; padding:4px 10px; font:inherit; cursor:pointer; }
  button:hover { border-color:var(--accent); }
  #scrub { flex:1; min-width:120px; accent-color:var(--accent); }
  #count { font-variant-numeric:tabular-nums; min-width:9ch; text-align:right; }
  #name { color:var(--muted); font-size:12px; }
  #help { color:var(--muted); font-size:12px; }
  #empty { color:var(--muted); }
  body.fs #bar { display:none; }
</style>
</head>
<body>
<div id="stage"><span id="empty">No captures yet.</span><img id="img" alt="" hidden></div>
<div id="bar">
  <button id="first" title="First (Home)">&#x23EE;</button>
  <button id="prev" title="Previous (&larr;)">&#x25C0;</button>
  <button id="play" title="Play / pause (Space)">&#x25B6; Play</button>
  <button id="next" title="Next (&rarr;)">&#x25B6;</button>
  <button id="last" title="Last (End)">&#x23ED;</button>
  <select id="speed" title="Seconds per slide">
    <option value="250">0.25 s</option><option value="500">0.5 s</option>
    <option value="1000" selected>1 s</option><option value="2000">2 s</option>
    <option value="5000">5 s</option>
  </select>
  <input id="scrub" type="range" min="1" max="1" value="1">
  <span id="count"></span>
  <button id="fs" title="Fullscreen (F)">&#x26F6;</button>
  <span id="name"></span>
  <span id="help">&larr;/&rarr; step &middot; Space play &middot; Home/End &middot; F fullscreen</span>
</div>
<script>
const FRAMES = __FRAMES__;
const img = document.getElementById("img"), scrub = document.getElementById("scrub");
const playBtn = document.getElementById("play"), speed = document.getElementById("speed");
let i = 0, timer = null;

function show(n) {
  if (!FRAMES.length) return;
  i = Math.max(0, Math.min(FRAMES.length - 1, n));
  img.src = FRAMES[i];
  img.hidden = false;
  document.getElementById("empty").hidden = true;
  scrub.value = i + 1;
  document.getElementById("count").textContent = (i + 1) + " / " + FRAMES.length;
  document.getElementById("name").textContent = FRAMES[i];
  new Image().src = FRAMES[Math.min(i + 1, FRAMES.length - 1)];  // preload next
}
function play(on) {
  clearInterval(timer); timer = null;
  if (on) {
    if (i >= FRAMES.length - 1) show(0);
    timer = setInterval(() => { if (i >= FRAMES.length - 1) play(false); else show(i + 1); },
                        +speed.value);
  }
  playBtn.innerHTML = timer ? "&#x23F8; Pause" : "&#x25B6; Play";
}
function toggleFs() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().catch(() => {});
}
document.addEventListener("fullscreenchange",
  () => document.body.classList.toggle("fs", !!document.fullscreenElement));

scrub.max = Math.max(1, FRAMES.length);
scrub.oninput = () => { play(false); show(scrub.value - 1); };
document.getElementById("first").onclick = () => { play(false); show(0); };
document.getElementById("prev").onclick = () => { play(false); show(i - 1); };
document.getElementById("next").onclick = () => { play(false); show(i + 1); };
document.getElementById("last").onclick = () => { play(false); show(FRAMES.length - 1); };
document.getElementById("fs").onclick = toggleFs;
document.getElementById("stage").onclick = () => { play(false); show(i + 1); };
playBtn.onclick = () => play(!timer);
speed.onchange = () => { if (timer) play(true); };
document.addEventListener("keydown", e => {
  if (e.target.tagName === "SELECT") return;
  const k = e.key;
  if (k === "ArrowRight" || k === "ArrowDown" || k === "PageDown") { play(false); show(i + 1); }
  else if (k === "ArrowLeft" || k === "ArrowUp" || k === "PageUp") { play(false); show(i - 1); }
  else if (k === " ") play(!timer);
  else if (k === "Home") { play(false); show(0); }
  else if (k === "End") { play(false); show(FRAMES.length - 1); }
  else if (k === "f" || k === "F") toggleFs();
  else return;
  e.preventDefault();
});
show(0);
</script>
</body>
</html>
"""


def write_slideshow(folder):
    """(Re)write slideshow.html listing every capture_*.png in `folder`."""
    folder = Path(folder)
    frames = sorted(p.name for p in folder.glob("capture_*.png"))
    page = (SLIDESHOW_TEMPLATE
            .replace("__TITLE__", html.escape(f"Captures - {folder.name}"))
            .replace("__FRAMES__", json.dumps(frames)))
    tmp = folder / ".slideshow.html.tmp"
    tmp.write_text(page, encoding="utf-8")
    tmp.replace(folder / "slideshow.html")
    return folder / "slideshow.html", len(frames)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def parse_args():
    ap = argparse.ArgumentParser(
        description="Repeatedly screenshot the screen and press the Down arrow key.")
    ap.add_argument("-n", "--count", type=int, default=300,
                    help="number of screenshots to take (default: 300)")
    ap.add_argument("-d", "--delay", type=float, default=5.0,
                    help="seconds to wait before the first capture (default: 5)")
    ap.add_argument("-s", "--settle", type=float, default=0.5,
                    help="seconds to wait after each key press before the next "
                         "capture, so the page can finish scrolling (default: 0.5)")
    ap.add_argument("-o", "--output", type=Path,
                    help="output folder (default: captures/<timestamp> next to this script)")
    ap.add_argument("--slideshow-only", type=Path, metavar="FOLDER",
                    help="just (re)build slideshow.html for an existing capture folder")
    args = ap.parse_args()
    if args.count < 1:
        ap.error("--count must be at least 1")
    return args


def main():
    args = parse_args()

    if args.slideshow_only:
        path, n = write_slideshow(args.slideshow_only)
        print(f"Wrote {path} ({n} frames)")
        return 0

    out = args.output or SCRIPT_DIR / "captures" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    width = max(4, len(str(args.count)))

    portal = Portal()
    keyboard = Keyboard(portal)
    try:
        print("Requesting keyboard access - approve the Remote Desktop dialog...")
        keyboard.start()

        # Warm-up capture: triggers the one-off screenshot permission prompt
        # now rather than mid-run, where it would steal focus from the browser.
        print("Checking screenshot permission...")
        warmup = out / ".warmup.png"
        take_screenshot(portal, warmup)
        warmup.unlink(missing_ok=True)

        print(f"Saving {args.count} captures to {out}")
        print("Click into the browser window now.")
        time.sleep(args.delay % 1)
        for remaining in range(int(args.delay), 0, -1):
            print(f"  starting in {remaining}...", flush=True)
            time.sleep(1)

        for n in range(1, args.count + 1):
            dest = out / f"capture_{n:0{width}d}.png"
            take_screenshot(portal, dest)
            write_slideshow(out)
            print(f"  [{n}/{args.count}] {dest.name}", flush=True)
            if n < args.count:
                keyboard.tap(XK_DOWN)
                time.sleep(args.settle)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except (PortalError, dbus.DBusException) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        keyboard.close()
        path, frames = write_slideshow(out)
        if frames:
            print(f"Slideshow: {path}  ({frames} frames)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
