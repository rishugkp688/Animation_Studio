"""Style-seam experiment: change body proportions through the rig tables only, then run layout QC
on both films. Usage: python3 benchmarks/experiments/rig_proportions.py baseline|long_legs|small_head
See Agent_Docs/06-style-seam.md."""
import json, sys, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from kit import rig
variant = sys.argv[1]
if variant == 'long_legs':          # leg 2.0 -> 2.6 studs, through PARTS/JOINTS only
    for k in ('leg_r', 'leg_l'):
        j, c, s = rig.PARTS[k]; rig.PARTS[k] = (j, (0, 0, -1.3), (s[0], s[1], 2.6))
    p, o = rig.JOINTS['pelvis']; rig.JOINTS['pelvis'] = (p, (0, 0, 2.6))
elif variant == 'small_head':       # head 1.22 -> 0.9 studs tall, 1.30 -> 1.0 wide
    j, c, s = rig.PARTS['head']; rig.PARTS['head'] = (j, (0, 0, 0.5), (1.0, 0.95, 0.9))
    rig.HEAD_HEIGHT = 0.9; rig.HEAD_RADIUS = 0.5
from kit.layout import compile_plan
from kit import qc
b = rig.Body()
print(variant, 'Body.leg', round(b.leg / b.s, 2), 'studs; PARTS leg', rig.PARTS['leg_r'][2][2], '; height', round(b.height / b.s, 2))
for film in ('one-hug-left', 'three-words'):
    sp = json.load(open(ROOT / f'episodes/{film}/screenplay.json'))
    tm = json.load(open(ROOT / f'episodes/{film}/voice/timing.json'))
    plan = compile_plan(sp, tm)
    m = qc.run(plan, step=2).merged()
    errs = [i for i in m if i['severity'] == 'error']
    warns = [i for i in m if i['severity'] != 'error']
    print(f'  {film}: {len(errs)} errors {dict(collections.Counter(e["rule"] for e in errs))}; warns {dict(collections.Counter(w["rule"] for w in warns))}')
    for e in errs[:4]:
        print('     ', e['shot'], e['rule'], e['msg'])
