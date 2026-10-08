# The vhs tape for the golden-path recording.
#
#   vhs docs/demo/golden-path.tape
#
# MODEL_PROVIDER=fake is the deterministic path: a scripted replay with no API
# key and a byte-identical trace every run. scripts/demo_golden_path.py serves
# the real API under uvicorn and drives the real worker loop in one process,
# because the SQLite vector store is in-process memory and a two-process
# deployment would abstain (docs/limitations.md). Every operator action in the
# recording is a real HTTP request to the running API.
#
# What the GIF shows: /ready certifying against the database, the ticket being
# submitted, the run parking at WAITING_APPROVAL, a human approving, the worker
# executing the refund exactly once, and the billing server reporting one
# refunded transaction.
#
# WHY THE COMMITTED GIF WAS NOT MADE WITH THIS TAPE
# -------------------------------------------------
# The tape is correct and is kept, but `vhs` cannot record on this machine
# (Windows 11), for two independent reasons, both verified:
#
#   1. Its terminal capture is blank. vhs drives ttyd, whose xterm.js defaults
#      to the WebGL renderer; in a headless browser with no GPU that renderer
#      draws no glyphs, so every captured frame is empty. Starting ttyd with
#      `-t rendererType=dom` renders correctly in the same browser, but vhs
#      hard-codes `rendererType=canvas` and exposes no way to override it.
#   2. Its GIF encoder writes nothing. `vhs` prints "Creating ...gif" and exits
#      0 with no file on disk, for a two-line tape as well as the real one, and
#      never spawns ffmpeg.
#
# So the committed docs/assets/golden-path.gif was recorded the same way vhs
# records -- screenshots of the real ttyd terminal while the real demo runs,
# driven by scripts/record_demo_gif.mjs -- and encoded with the system ffmpeg.
# Nothing in it is drawn by hand. Re-record it with that script, not with vhs:
#
#   node scripts/record_demo_gif.mjs

Output docs/assets/golden-path.gif

Set Shell "bash"
Set FontSize 16
Set FontFamily "Cascadia Mono"
Set Width 1500
Set Height 1080
Set Padding 24
Set Margin 10
Set MarginFill "#1B1B24"
Set BorderRadius 8
Set Framerate 24
Set PlaybackSpeed 1.4
Set TypingSpeed 35ms
Set CursorBlink false
Set Theme { "name": "opspilot", "background": "#1B1B24", "foreground": "#D6D6DD", "cursor": "#7AA2F7", "black": "#1B1B24", "red": "#F7768E", "green": "#9ECE6A", "yellow": "#E0AF68", "blue": "#7AA2F7", "magenta": "#BB9AF7", "cyan": "#7DCFFF", "white": "#D6D6DD" }

# A clean prompt. The script needs no arguments and reads its configuration from
# pinned environment variables inside the script itself.
Hide
Type "clear"
Enter
Show

Type "./.venv/Scripts/python.exe scripts/demo_golden_path.py"
Enter
Sleep 12s
