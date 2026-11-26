# Design department — art director, character, prop and set designers, lighting

## House look: Roblox R6, story-reel finish

- **Bodies are true R6**: legs 1×1×2 studs, torso 2×1×2, arms 1×1×2, a rounded cylinder
  head, one stud = 0.33 m at scale 1. Children use `scale` 0.72–0.82, adults 0.95–1.0.
  `build` widens a heavier torso (1.0–1.08). Never change proportions per shot.
- **Faces are decals**, like real Roblox avatars: ink eyes with moving pupils, brows,
  16 eye shapes, 16 mouths (including lip-sync shapes), tears, blush. Drawn once in
  `kit/faces.py` and shared by everyone, so expressions stay on model.
- **Clothes are blocks and trims**: hoodie (pocket, strings, hood), cardigan (inner shirt,
  buttons), shirt (placket, collar, belt), sweater, tee (print), hospital gown. Sleeves end
  in skin-coloured hands; legs end in shoes with soles.
- **Hair is chunky geometry**: `spiky`, `short`, `bob`, `long`, `pigtails`, `bun`,
  `bald_sides`. Fringes must stop above the brows (the eyes carry the acting).
- **Overhead counters** (when the story has a visible rule) are white with an ink outline,
  face the camera and keep their screen size readable in wide shots.

## Cast entry

```json
"kai": {"name": "Kai", "role": "lead, age 10", "scale": 0.8,
  "look": {"skin": "#E9BE95", "hair": {"style": "spiky", "color": "#2B211D"},
           "glasses": "#3B3B3B", "mustache": "#D4D4D2", "beard": "#1F1A18", "wrinkles": true, "freckles": true,
           "outfits": {"default": {"top": {"style": "hoodie", "color": "#3D72D9", "trim": "#E9EEF7"},
                                   "pants": "#24272E", "shoes": "#F1F1F1"},
                       "gown": {...}}},
  "badge": true}
```

Change outfits per scene with `"wardrobe": {"grandpa": "gown"}` on the scene. Give every
lead one strong colour that no one else in the film wears (Kai is the only blue hoodie),
so a viewer can find them in any wide shot.

## Sets

A set is data in `scripts/kit/sets.py`: geometry elements, `marks` (named standing
spots with a facing), `seats`, `points` (things to look at), lights, walkable `bounds`,
and `times` (sky, sun, exposure per time of day). The same data drives the Blender
build, the walking routes, the collision checks and the camera solver.

Rules for a new set:

1. **R6 scale.** Characters are 1.3 m wide at the shoulders. Doors at least 1.35 m,
   chairs ≥ 1 m apart, aisles ≥ 1.5 m. A real-scale door will trap actors.
2. **One landmark** the audience recognises from any angle (the school arch and sign, the
   green armchair and lamp, the hospital bed and monitor).
3. **Walls that may be removed for a camera** get `cut=(inward normal)`. The camera
   solver hides them only for shots that need it. Ceilings get `cut=(0, 0, -1)`.
4. **Mark everything that collides** `solid` (default for boxes). Purely visual dressing
   (books, frames, leaves, text) is `solid=False`. Seats carry `tag="seat"`.
5. **Sound of the place**: set `s.surface` (concrete, wood, vinyl) and `s.reverb`
   (IR seconds, wet) so footsteps and dialogue sound right there.
6. **Light motivated by the place**: a window, a lamp, ceiling panels. Interiors use
   area lights at 60–150 W; night scenes need a key light near the action or faces go black.
7. Add the function to `LIBRARY`, render an occupied wide and a close-up with
   `blender_build.py --stills`, and look at them at phone size before writing scenes there.

Library today: `school_gate` (morning or afternoon exterior), `grandpa_living` (afternoon interior),
`hospital_room` (night), `hospital_corridor` (night).

## Lighting

Eevee, AgX view transform with *Medium High Contrast*. Each set's `times` preset sets the
world gradient, sun angle/colour/power; set lights are local. Depth of field is automatic
on the shot subject's eyes (f/2–2.8 for close-ups). Check the darkest shot first.

## Design review (what to look at)

- Lineup at phone size: can you tell every character apart by silhouette and colour?
- Close-up: are the eyes clear of hair and glasses? Do the expressions read?
- Occupied wide in each set: is the landmark visible? Does the light make sense?
