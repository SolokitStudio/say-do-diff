"""Render docs/demo.gif from the real CLI output (no mock text)."""
# Requires Pillow and DejaVu Sans Mono. Run: python3 docs/make_demo_gif.py
import subprocess, sys, os
from PIL import Image, ImageDraw, ImageFont

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 15)
BOLD = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 15)
CW = FONT.getbbox("M")[2]; LH = 21
COLS, ROWS = 108, 25
PAD, BAR = 18, 30
W, H = PAD * 2 + CW * COLS, BAR + PAD * 2 + LH * ROWS
BG, FG, DIM = (22, 27, 34), (201, 209, 217), (125, 133, 144)
RED, GREEN, YEL, BLUE = (255, 123, 114), (126, 231, 135), (227, 179, 65), (121, 192, 255)

def sh(cmd):
    p = subprocess.run(cmd, shell=True, cwd=REPO, capture_output=True, text=True)
    return p.stdout.rstrip("\n").split("\n"), p.returncode

reply, _ = sh("cat examples/reply.txt")
out, code = sh("python3 -m saydo_diff --message examples/reply.txt --log examples/tool_log.json")
assert code == 2, code

def color(line):
    if line.startswith("$ "): return [("$ ", BLUE, True), (line[2:], FG, True)]
    if line.startswith("[GAP]"): return [("[GAP]", RED, True), (line[5:], FG, False)]
    if line.startswith("[ok ]"): return [("[ok ]", GREEN, True), (line[5:], FG, False)]
    if line.startswith("[DUP]"): return [("[DUP]", YEL, True), (line[5:], FG, False)]
    if "STOP-SHIP" in line:
        i = line.index("STOP-SHIP"); return [(line[:i], FG, True), (line[i:], RED, True)]
    if line.startswith("       \""): return [(line, DIM, False)]
    return [(line, FG, False)]

def wrap(line):
    return [line[i:i + COLS] for i in range(0, max(len(line), 1), COLS)] or [""]

def frame(lines, cursor=False):
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, BAR], fill=(13, 17, 23))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([14 + i * 20, 10, 26 + i * 20, 22], fill=c)
    d.text((W // 2, BAR // 2), "say-do-diff", fill=DIM, font=FONT, anchor="mm")
    rows = []
    for ln in lines:
        segs = color(ln); flat = "".join(s for s, _, _ in segs)
        if len(flat) <= COLS: rows.append(segs)
        else:
            col = segs[0][1]; rows += [[(w, col if ln.startswith("$") else FG, False)] for w in wrap(flat)]
    rows = rows[-ROWS:]
    y = BAR + PAD
    for segs in rows:
        x = PAD
        for s, c, b in segs:
            d.text((x, y), s, fill=c, font=BOLD if b else FONT); x += CW * len(s)
        y += LH
    if cursor:
        x = PAD + CW * len("".join(s for s, _, _ in rows[-1])) if rows else PAD
        d.rectangle([x, y - LH + 3, x + CW - 2, y - 3], fill=FG)
    return im

frames, durs = [], []
def add(lines, ms, cursor=False):
    frames.append(frame(lines, cursor)); durs.append(ms)

screen = []
def type_cmd(cmd):
    global screen
    base = list(screen)
    step = 3
    for i in range(0, len(cmd) + 1, step):
        add(base + ["$ " + cmd[:i]], 45, cursor=True)
    screen = base + ["$ " + cmd]
    add(screen, 350)

add(["$ "], 700, cursor=True)
type_cmd("cat examples/reply.txt")
screen += reply + [""]; add(screen, 2200)
type_cmd("saydo-diff --message examples/reply.txt --log examples/tool_log.json")
for ln in out:
    screen.append(ln); add(screen, 260)
screen += [""]; add(screen, 600)
type_cmd("echo $?")
screen += [str(code)]; add(screen + ["$ "], 6000, cursor=True)

pal = frames[-1].convert("P", palette=Image.ADAPTIVE, colors=255)
qs = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
dst = os.path.join(REPO, "docs", "demo.gif")
qs[0].save(dst, save_all=True, append_images=qs[1:], duration=durs, loop=0, optimize=True)

print(dst, len(frames), "frames", os.path.getsize(dst) // 1024, "KB", W, "x", H)
