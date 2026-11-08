# Screen_capture

Capture everything visible on a long web page by repeatedly taking a screenshot
and pressing the **Down arrow** key, then review the results as a slideshow.

Built for **GNOME on Wayland** (Ubuntu 24.04+), where tools such as `xdotool`
and `scrot` cannot see or drive other windows. The script uses
xdg-desktop-portal over D-Bus instead, so it needs no extra packages beyond the
system `python3-dbus` and `python3-gi`.

## Files

- [screen_capture.py](screen_capture.py): capture loop and slideshow generator

## Usage

```bash
./screen_capture.py                  # 300 captures, 5 s start delay
./screen_capture.py -n 50            # 50 captures
./screen_capture.py -s 1.0           # wait 1 s after each key press (slow pages)
./screen_capture.py -o ~/my_capture  # choose the output folder
./screen_capture.py -k right -s 1.5  # paged viewer (e.g. Kindle Cloud Reader): Right arrow
```

| Option | Default | Meaning |
|---|---|---|
| `-n`, `--count` | `300` | Number of screenshots |
| `-d`, `--delay` | `5` | Seconds before the first capture |
| `-s`, `--settle` | `0.5` | Seconds after each key press before the next capture |
| `-k`, `--key` | `down` | Key pressed between captures: `down`, `right`, `pagedown`, `space` |
| `-o`, `--output` | `captures/<timestamp>/` | Output folder |
| `--slideshow-only FOLDER` | | Rebuild `slideshow.html` for an existing folder |

### What happens

1. GNOME shows a **Remote Desktop** dialog asking to allow keyboard control. Approve it.
2. On the first run only, GNOME may also ask to **allow screenshots**.
3. A 5-second countdown starts. Click into the browser window.
4. The loop runs: screenshot → save `capture_0001.png` → Down arrow → wait → repeat.
   A screen-sharing indicator shows in the top bar while the session is active.
5. Press `Ctrl+C` in the terminal to stop early. Everything captured so far is kept.

## Slideshow

Each capture folder contains a `slideshow.html`, updated after every frame.
Open it in a browser:

```bash
xdg-open captures/<timestamp>/slideshow.html
```

Controls: `←`/`→` step · `Space` play/pause · `Home`/`End` · `F` fullscreen ·
click the image to advance · drag the slider to scrub · choose seconds per slide.
