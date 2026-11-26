"""One place to find the studio's fonts on any Linux machine.

The house face is Fira Sans. Its path differs between distributions (Arch puts it in
/usr/share/fonts/TTF, Debian/Ubuntu in /usr/share/fonts/opentype/fira), so every stage asks
this module instead of hard-coding a path. Order of preference:

1. the environment overrides BLOCKY_FONT_BOLD / BLOCKY_FONT_REGULAR / BLOCKY_FONT_EXTRABOLD,
2. known locations of Fira Sans, then Noto Sans, then DejaVu Sans,
3. fontconfig (`fc-match`), which always answers with some installed sans.

Pure Python: no bpy, no Pillow.
"""

import os
import shutil
import subprocess
from pathlib import Path

_DIRS = ["/usr/share/fonts/TTF", "/usr/share/fonts/opentype/fira", "/usr/share/fonts/truetype/fira",
         "/usr/share/fonts/fira-sans", "/usr/share/fonts/noto", "/usr/share/fonts/truetype/noto",
         "/usr/share/fonts/google-noto", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
         "/usr/local/share/fonts", str(Path.home() / ".local/share/fonts"), str(Path.home() / ".fonts")]

# (family, file stem) in order of preference, per weight.
_FACES = {
    "extrabold": [("Fira Sans", "FiraSans-ExtraBold"), ("Fira Sans", "FiraSans-Bold"),
                  ("Noto Sans", "NotoSans-ExtraBold"), ("Noto Sans", "NotoSans-Bold"), ("DejaVu Sans", "DejaVuSans-Bold")],
    "bold": [("Fira Sans", "FiraSans-Bold"), ("Noto Sans", "NotoSans-Bold"), ("DejaVu Sans", "DejaVuSans-Bold")],
    "regular": [("Fira Sans", "FiraSans-Regular"), ("Noto Sans", "NotoSans-Regular"), ("DejaVu Sans", "DejaVuSans")],
}
_FC = {"extrabold": "sans:weight=205", "bold": "sans:bold", "regular": "sans"}


def find(weight="bold", dirs=None, env=None):
    """Return (path, family) for a weight, or (None, None) if the machine has no usable font."""
    env = os.environ if env is None else env
    override = env.get(f"BLOCKY_FONT_{weight.upper()}")
    if override and Path(override).is_file():
        return override, env.get("BLOCKY_FONT_FAMILY", Path(override).stem.split("-")[0])
    for family, stem in _FACES[weight]:
        for d in (dirs or _DIRS):
            for ext in (".ttf", ".otf"):
                p = Path(d) / (stem + ext)
                if p.is_file():
                    return str(p), family
    if dirs is None and shutil.which("fc-match"):
        r = subprocess.run(["fc-match", "-f", "%{file}\n%{family[0]}", _FC[weight]], capture_output=True, text=True)
        parts = r.stdout.split("\n")
        if r.returncode == 0 and len(parts) >= 2 and Path(parts[0]).is_file():
            return parts[0], parts[1]
    return None, None


def require(weight="bold"):
    path, family = find(weight)
    if not path:
        raise SystemExit(f"no {weight} sans font found; install Fira Sans or set BLOCKY_FONT_{weight.upper()}")
    return path, family
