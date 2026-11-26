"""Word-timed vertical captions (ASS) from the plan's aligned lines.

Short chunks (<= 3 words) pop in, the word being spoken turns gold, and the
text sits at ~31% height: below faces, above the Reels/TikTok bottom UI.
Narration is white; on-screen dialogue is tinted per speaker and italic.
"""

import json
import re
import sys
from pathlib import Path

HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: VO,Fira Sans,86,&H00FFFFFF,&H00FFFFFF,&H00101010,&H64000000,-1,0,0,0,100,100,1,0,1,7,3,2,90,90,600,1
Style: DLG,Fira Sans,82,&H00FFFFFF,&H00FFFFFF,&H00101010,&H64000000,-1,-1,0,0,100,100,1,0,1,7,3,2,90,90,600,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
GOLD = "&H0047D6FF&"      # ASS colours are BGR
# Dialogue tints (BGR), assigned to speakers in order of first line; the narrator's
# dialogue lines get the first tint. A cast entry may set "caption_color": "#RRGGBB".
TINTS = ["&H00FFE0A6&", "&H00A8DDFF&", "&H00C8B4FF&", "&H00B4FFC8&", "&H00FFC8F0&"]


def speaker_tints(plan, cast=None):
    order = []
    narrator = plan.get("narrator")
    if narrator:
        order.append(narrator)
    for L in plan["lines"]:
        if L["kind"] != "vo" and L["who"] not in order:
            order.append(L["who"])
    out = {}
    for i, who in enumerate(order):
        hexc = ((cast or {}).get(who) or {}).get("caption_color")
        if hexc:
            h = hexc.lstrip("#")
            out[who] = f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&".upper()
        else:
            out[who] = TINTS[i % len(TINTS)]
    return out


def ts(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def chunks(words, max_words=3, max_chars=16):
    out, cur = [], []
    for w in words:
        cur.append(w)
        text = " ".join(x["w"] for x in cur)
        end_punct = re.search(r"[.,!?…]$", w["w"])
        if len(cur) >= max_words or len(text) >= max_chars or end_punct:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def build(plan, narrator=None, cast=None):
    tints = speaker_tints(plan, cast)
    events = []
    for L in plan["lines"]:
        words = L.get("words") or []
        if not words:
            continue
        style = "VO" if L["kind"] == "vo" else "DLG"
        base = "&H00FFFFFF&" if style == "VO" else tints.get(L["who"], "&H00FFFFFF&")
        groups = chunks(words)
        for gi, g in enumerate(groups):
            start = g[0]["s"] - 0.03
            nxt = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else L["t1"] + 0.25
            end = min(max(g[-1]["e"] + 0.12, start + 0.45), nxt - 0.02)
            for wi, w in enumerate(g):
                a = start if wi == 0 else w["s"]
                b = g[wi + 1]["s"] if wi + 1 < len(g) else end
                parts = []
                for wj, x in enumerate(g):
                    word = x["w"].upper() if style == "VO" else x["w"]
                    if wj == wi:
                        parts.append("{\\c" + GOLD + "}" + word + "{\\c" + base + "}")
                    else:
                        parts.append(word)
                pop = "{\\fscx82\\fscy82\\t(0,90,\\fscx100\\fscy100)}" if wi == 0 else ""
                text = pop + "{\\c" + base + "}" + " ".join(parts)
                events.append([a, b, style, text])
    # Never show two caption events at once: each starts after the previous one ends.
    events.sort(key=lambda e: e[0])
    for prev, cur in zip(events, events[1:]):
        if cur[0] < prev[1]:
            prev[1] = cur[0]
    out = [f"Dialogue: 0,{ts(a)},{ts(b)},{st},,0,0,0,,{tx}" for a, b, st, tx in events if b - a > 0.02]
    return HEAD + "\n".join(out) + "\n"


if __name__ == "__main__":
    plan = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[2]).write_text(build(plan, None))
