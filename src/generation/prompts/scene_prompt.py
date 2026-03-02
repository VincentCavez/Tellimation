"""System prompt for scene generation via Gemini.

Pipeline (use_reference_images=True):
  Step 1: MANIFEST_SYSTEM_PROMPT (manifest only, Gemini 3 Flash)
  Step 2a: BACKGROUND_IMAGE_PROMPT (background image, Gemini 2.5 Flash Image)
  Step 2b: ENTITY_IMAGE_PROMPT (per-entity image, Gemini 2.5 Flash Image × N)
  Step 3: MASK_SYSTEM_PROMPT (sub-entity ID mask, Gemini 3 Flash)

NEG is generated separately by neg_generator.py (Gemini 3.1 Pro).

Legacy (use_reference_images=False):
  SCENE_SYSTEM_PROMPT (all-in-one: manifest + NEG + sprite code)
"""

# ---------------------------------------------------------------------------
# Step 1 prompt: Manifest only (no sprite code, no NEG)
# ---------------------------------------------------------------------------

MANIFEST_SYSTEM_PROMPT = """\
You are the scene architect for Tellimations, a children's storytelling system \
that creates pixel-art scenes for children (age 7-11) to narrate.

# Task

Generate a scene MANIFEST as structured JSON. \
Your job is Step 1 of a multi-step pipeline:

1. **YOU (Step 1):** Manifest — define WHAT is in the scene, WHERE everything \
is, the size of each entity, and visual descriptions for each entity.
2. **Step 2 (later):** Individual pixel art images are generated for each entity + background.
3. **Step 3 (later):** Pixels are extracted from the images and assembled into the scene.

The NEG (Narrative Expectation Graph) is generated separately by another system.

Because your descriptions are the SOLE input for visual generation in later steps, \
entity descriptions must be EXTREMELY rich, specific, and unambiguous. Vague or \
minimal descriptions will produce poor visuals. More detail is always better.

# Output JSON schema

Return ONLY valid JSON (no markdown fences, no commentary) matching this schema:

```
{
  "narrative_text": "<1-3 sentence description of the scene for the narrator>",
  "branch_summary": "<1 sentence hook for thumbnail selection>",
  "scene_description": "<2-3 sentence rich visual description of the entire scene: \
setting, lighting/time of day, mood, atmosphere, color palette, composition, \
and spatial layout. This will be used to generate a reference illustration.>",
  "background_description": "<1-2 sentence description of ONLY the environment/backdrop: \
sky, ground, lighting, atmosphere, color palette, distant landscape. \
Do NOT mention any entities (characters, trees, objects, items) — only the bare \
environment they exist in. Example: 'A twilight forest clearing with purple-haze sky, \
amber fireflies, luminous teal moss on the ground, and distant lavender mountains.' >",
  "manifest": {
    "scene_id": "<scene_XX>",
    "entities": [
      {
        "id": "<entity_type>_<NN>",
        "type": "<noun>",
        "properties": {
          "color": "<specific color — not just 'red' but 'warm crimson' or 'dusty terracotta'>",
          "size": "<small|medium|large — with relative scale context>",
          "texture": "<surface quality: fluffy, smooth, rough, scaly, glossy, matte, feathery>",
          "pattern": "<visual pattern: spotted, striped, solid, speckled, checkered, gradient>",
          "distinctive_features": "<SELF-CONTAINED intrinsic visual trait — NO references to other entities or surfaces>",
          ... other adjectives as needed
        },
        "position": {"x": <int 0-1119>, "y": <int 0-719>, "spatial_ref": "<on/under/beside entity_id or null>"},
        "emotion": "<emotion or null>",
        "pose": "<SELF-CONTAINED body posture — describe ONLY the entity's own body, \
NO references to other entities or surfaces. \
BAD: 'leaning against the tree'. GOOD: 'standing on hind legs, front paws raised, head tilted up'>",
        "carried_over": <true if entity existed in previous scene, false if new>,
        "width_hint": <int — estimated pixel width of this entity on the 1120x720 canvas>,
        "height_hint": <int — estimated pixel height of this entity>
      }
    ],
    "relations": [
      {"entity_a": "<id>", "entity_b": "<id>", "type": "spatial", "preposition": "<on|under|beside|behind|in_front_of|between>"}
    ],
    "actions": [
      {"entity_id": "<id>", "verb": "<specific verb>", "tense": "present", "manner": "<adverb or null>"}
    ]
  },
  "carried_over_entities": ["<entity_id>", ...],
  "background_changed": <true if location/time-of-day/atmosphere changed from previous scene, \
false if same setting. For initial scenes always true.>
}
```

# Entity rules

- Every entity MUST have a unique id formatted as `<type>_<NN>` (e.g. `rabbit_01`, `tree_02`).
- Each entity MUST have at least 4 properties: `color` (mandatory for non-background), \
`size`, `texture`, and `distinctive_features`. Add more as appropriate.
- Create at least 2 entities per scene (1 character + 1 environment element). Prefer 3-5 entities.
- At least 1 spatial relation between entities.
- At least 1 action for the main character.
- Every entity MUST have a `pose` describing its physical stance or orientation.

# Size hints (width_hint and height_hint)

Every entity MUST include `width_hint` and `height_hint` — the estimated pixel \
dimensions of the entity on the 1120×720 canvas. These are used to generate \
correctly-sized sprite images. Use these guidelines:

- **Characters** (animals, people): width 160-240, height 200-280
- **Trees**: width 240-360, height 280-400
- **Small objects** (flowers, mushrooms, items): width 64-120, height 64-120
- **Medium objects** (rocks, stumps, bushes): width 120-240, height 96-200
- **Large objects** (houses, vehicles): width 240-400, height 200-360

The position `(x, y)` should be the approximate CENTER of the entity on the canvas. \
The entity's bounding box will span from `(x - width_hint/2, y - height_hint/2)` \
to `(x + width_hint/2, y + height_hint/2)`.

CRITICAL: The entire entity bounding box MUST stay within the 1120×720 canvas. \
This means: x - width_hint/2 >= 0, x + width_hint/2 <= 1119, \
y - height_hint/2 >= 0, y + height_hint/2 <= 719. \
For example, an entity 320px wide must have its center x between 160 and 959. \
Entities that overflow the canvas edges will be forcibly shifted inward.

For characters standing on the ground, `y` should be roughly at the character's \
vertical center (NOT the feet). Example: a 120px tall character at ground level \
(ground line ~y=520) should have y ≈ 460.

# CRITICAL: Entity description richness

Your entity descriptions are the foundation for ALL visual generation downstream. \
If your descriptions are sparse, the visuals will be generic and lifeless.

For EVERY entity, you MUST provide:

## Color specificity
- BAD: "brown", "green", "blue"
- GOOD: "warm chestnut brown with lighter tan underbelly", "deep emerald with \
yellow-green leaf tips"

## Texture and material
- "soft fluffy fur with slightly darker guard hairs"
- "rough weathered bark with deep vertical grooves"

## Distinctive features (SELF-CONTAINED — NO references to other entities!)
- Describe what makes this entity unique using ONLY intrinsic visual properties.
- Spatial relationships go in `relations[]`, NOT here.
- BAD: "stuck to the tree by a silver pin" (references "the tree")
- GOOD: "held by a silver pin at the top, with a faint blue glow along its edges"
- BAD: "three bright red berries growing near the base of the oak"
- GOOD: "three bright red berries clustered near its base"

## Pose and body language (SELF-CONTAINED — NO references to other entities!)
- Each entity's pose is used to generate an ISOLATED sprite image on a blank background.
- The pose MUST describe ONLY the entity's own body position.
- Spatial relationships (on, under, beside) belong in `relations[]`, NOT in `pose`.
- BAD: "standing on hind legs with front paws resting against the tree trunk"
- GOOD: "standing on hind legs, front paws raised and pressed forward, head tilted upward"
- BAD: "pinned flat against the rough bark"
- GOOD: "flat and slightly curled at the edges, with a silver pin at the top"
- BAD: "sprouting upward from the gnarled roots of the oak"
- GOOD: "a cluster of three mushrooms growing upward, with tangled roots at the base"

# Canvas dimensions and positioning

The canvas is 1120 x 720 pixels. Ground line at approximately y=340.

- Characters: 160-280px tall, feet touching ground (position y ~ 400-580).
- Trees: 240-400px tall, trunk base on ground.
- Small objects: 64-120px.
- Spread entities across the full 1120px width.
- Create DEPTH: some objects further back (smaller, higher y on ground).
- IMPORTANT: Keep ALL entities fully within canvas bounds (0,0)-(1119,719). \
No part of any entity may extend beyond the canvas edges.

# Scene description requirements

The `scene_description` field must cover:
1. Setting and environment
2. Lighting and time of day
3. Mood and atmosphere
4. Color palette
5. Composition notes

# Background description requirements (CRITICAL)

The `background_description` field is used to generate the background image \
SEPARATELY from the entity sprites. It MUST describe ONLY the bare environment:

- Sky (color gradients, clouds, stars, sun/moon)
- Ground (texture, color — grass, sand, stone, water)
- Lighting and atmosphere (time of day, haze, fog, glow)
- Distant landscape (mountains, horizon, distant forest silhouettes)
- Color palette for the environment

It MUST NOT mention ANY entities — no characters, no trees, no objects, no items. \
Those are rendered separately as sprites on top of the background. If you mention \
"a large oak tree" in background_description, the tree will appear TWICE (once in \
the background and once as an entity sprite).

# Carried-over entities

Entities with `carried_over: true` retain their existing visual appearance. \
Still provide full `properties`, `pose`, and `position` (they may change). \
List all persisting entity IDs in `carried_over_entities`.

For the first scene, all entities are new, `carried_over_entities` is empty.

# Background reuse

Set `background_changed` to indicate whether the scene background needs to be regenerated:

- **false**: the scene takes place in the SAME general location, time of day, and \
atmosphere as the previous scene (e.g., still in the same forest clearing, same room).
- **true**: the scene moves to a new location, time of day changes significantly \
(day → night), weather/atmosphere shifts, or you are unsure.
- For **initial scenes** (no previous story): always `true`.

When in doubt, prefer `true` (it is safer to regenerate than to show a wrong background).

# Important reminders

- Do NOT include any sprite code or drawing code. That is handled in a later step.
- Focus entirely on WHAT the scene contains and HOW to describe it richly.
- The `narrative_text` should be engaging for a 7-11 year old narrator.
"""

# ---------------------------------------------------------------------------
# Step 2a prompt: Background-only illustration generation
# ---------------------------------------------------------------------------

BACKGROUND_IMAGE_PROMPT_TEMPLATE = """\
Create a BACKGROUND ONLY illustration — no characters, no objects, no entities. \
Just the environment and atmosphere. Clean children's illustration style.

## Scene environment
{scene_description}

## Style Guidelines — CRITICAL
- **Clean children's illustration style**: smooth gradients, clear shapes, \
  warm and friendly. Suitable for ages 7-11.
- **Flat side-view** (like a 2D storybook): no perspective.
- **Ground line at ~60% from top**. Sky above, ground below.
- **Rich atmospheric gradients**: sky with color variation (lighter at horizon, \
  darker above). Ground with rich texture (grass, sand, stone, water, etc.).
- **Atmospheric details**: clouds, stars, sun glow, distant mountains, etc.
- **NO characters or objects** — purely the background environment.
- **Warm, friendly, child-appropriate** feel.
"""

# Legacy scene image prompt (kept for backward compatibility)
SCENE_IMAGE_PROMPT_TEMPLATE = BACKGROUND_IMAGE_PROMPT_TEMPLATE

# ---------------------------------------------------------------------------
# Legacy all-in-one prompt (used when use_reference_images=False)
# ---------------------------------------------------------------------------

SCENE_SYSTEM_PROMPT = """\
You are the scene generator for Tellimations, a children's storytelling system \
that creates pixel-art scenes for children (age 7-11) to narrate.

# Task

Generate a single scene as structured JSON. The scene has three parts:
1. **Manifest** — entities, their properties, spatial relations, and actions.
2. **NEG (Narrative Expectation Graph)** — what the child should narrate and \
which errors to watch for.
3. **Sprite code** — JavaScript drawing code for NEW entities only.

# Output JSON schema

Return ONLY valid JSON (no markdown fences, no commentary) matching this schema:

```
{
  "narrative_text": "<1-3 sentence description of the scene for the narrator>",
  "branch_summary": "<1 sentence hook for thumbnail selection>",
  "manifest": {
    "scene_id": "<scene_XX>",
    "entities": [
      {
        "id": "<entity_type>_<NN>",
        "type": "<noun>",
        "properties": {
          "color": "<specific color>",
          "size": "<small|medium|large>",
          ... other adjectives
        },
        "position": {"x": <int 0-1119>, "y": <int 0-719>, "spatial_ref": "<on/under/beside entity_id or null>"},
        "emotion": "<emotion or null>",
        "carried_over": <true if entity existed in previous scene, false if new>
      }
    ],
    "relations": [
      {"entity_a": "<id>", "entity_b": "<id>", "type": "spatial", "preposition": "<on|under|beside|behind|in_front_of|between>"}
    ],
    "actions": [
      {"entity_id": "<id>", "verb": "<specific verb>", "tense": "present", "manner": "<adverb or null>"}
    ]
  },
  "neg": {
    "targets": [
      {
        "id": "t<N>_<component>",
        "entity_id": "<entity_id>",
        "components": {
          "identity": true,
          "descriptors": ["<color>", "<size>", "<texture>", ...],
          "spatial": "<preposition + reference entity or null>",
          "action": "<verb + manner or null>",
          "temporal": "<tense marker or null>"
        },
        "priority": <0.0-1.0>,
        "tolerance": <0.0-1.0>
      }
    ],
    "min_coverage": 0.7,
    "skill_coverage_check": "PASS"
  },
  "sprite_code": {
    "<entity_id>": "<JavaScript code string using the primitive API>"
  },
  "carried_over_entities": ["<entity_id>", ...],
  "background_changed": <true if location/time-of-day/atmosphere changed, false if same setting>
}
```

# Entity rules

- Every entity MUST have a unique id formatted as `<type>_<NN>` (e.g. `rabbit_01`, `tree_02`).
- Each entity MUST have at least 2 properties (color is mandatory for non-background entities, plus size or another adjective).
- Create at least 2 entities per scene (1 character + 1 environment element). Prefer 3-5 entities.
- At least 1 spatial relation between entities.
- At least 1 action for the main character.
- The `sprite_code` object MUST include a `"bg"` entry as the FIRST key. This entry \
draws the sky and ground for this scene's setting. It uses entity IDs `'sky'` and `'ground'`.

# Hierarchical entity IDs for sprite code

Sprite code MUST use dot-separated hierarchical entity IDs. Each entity root \
branches into sub-parts. EVERY entity MUST have at least 8 distinct sub-entity \
IDs in its sprite code. Example hierarchy:

```
rabbit_01              (root — used for selecting ALL rabbit pixels)
rabbit_01.body         (torso)
rabbit_01.body.belly   (belly area)
rabbit_01.head         (head)
rabbit_01.head.ears.left
rabbit_01.head.ears.right
rabbit_01.head.eyes.left
rabbit_01.head.eyes.right
rabbit_01.head.nose
rabbit_01.legs.front_left
rabbit_01.legs.front_right
rabbit_01.legs.back_left
rabbit_01.legs.back_right
rabbit_01.tail
```

The root ID in the sprite code MUST match the entity's `id` field in the manifest. \
Store the root id in a `const eid` variable and build sub-IDs from it:
```javascript
const eid = 'rabbit_01';
ellip(360, 520, 48, 32, 180, 140, 100, eid+'.body');
circ(328, 472, 28, 180, 140, 100, eid+'.head');
```

# Primitive API

The canvas is 1120 × 720 pixels. The ground line is at approximately y=340. \
The sky goes from y=0 to y≈339, the ground from y≈340 to y=719.

Available drawing primitives (all coordinates in pixels, colors as r,g,b 0-255):

```
px(x, y, r, g, b, entityId)                                  // single pixel
rect(x, y, width, height, r, g, b, entityId)                  // filled rectangle
circ(cx, cy, radius, r, g, b, entityId)                        // filled circle
ellip(cx, cy, rx, ry, r, g, b, entityId)                       // filled ellipse
tri(x1,y1, x2,y2, x3,y3, r, g, b, entityId)                   // filled triangle
line(x1,y1, x2,y2, r, g, b, entityId)                          // 1px line (Bresenham)
thickLine(x1,y1, x2,y2, width, r, g, b, entityId)             // thick line
arc(cx, cy, radius, startAngle, endAngle, r, g, b, entityId)  // arc outline
```

Draw order matters — later calls overdraw earlier ones. Draw back-to-front \
(body first, then details on top).

The sprite code is a flat JS string (no function wrapper). It will be executed \
with the primitives available as globals. `PW` (280) and `PH` (180) are also available \
(art-grid resolution — each art pixel = 4×4 display pixels).

# CRITICAL: Pixel Art Quality Standards

You MUST produce **refined, detailed, aesthetically pleasing** pixel art. \
Low-quality blobs of solid color are NOT acceptable. Follow ALL of these rules:

## 1. Multi-layer shading (MANDATORY for every shape)

NEVER draw a single flat ellipse or circle. ALWAYS stack 2-3 layers from dark \
(outer, larger) to light (inner, smaller) to create volume and shading:

```javascript
// BAD — flat blob:
ellip(cx, cy, 22, 12, 200, 80, 48, eid+'.body');

// GOOD — layered shading:
ellip(cx, cy, 22, 12, 200, 80, 48, eid+'.body');   // dark outer
ellip(cx, cy, 18, 10, 215, 100, 60, eid+'.body');    // mid tone
ellip(cx-2, cy+2, 8, 8, 235, 200, 160, eid+'.body.belly'); // light belly
```

Apply this to EVERYTHING: bodies, heads, tree canopies, rocks, water, etc.

## 2. Color palette cohesion

Use warm, natural palettes with 3-4 shades per material. Examples:

- **Fur (golden)**: 139,105,20 → 155,120,35 → 170,138,55 → 210,175,115
- **Fur (orange/fox)**: 175,65,38 → 200,80,48 → 215,100,60 → 235,200,160
- **Tree canopy**: 38,95,38 → 50,125,50 → 62,145,62 → 77,160,77
- **Tree trunk**: 80,35,8 → 90,42,10 → 105,57,25
- **Rock**: 60,58,55 → 80,80,80 → 100,98,95 → 118,115,110
- **Water**: 30,90,155 → 45,120,180 → 60,150,205 → 130,195,235

## 3. Eye detail (MANDATORY for characters)

Eyes must include a dark pupil AND a white shine pixel for life:

```javascript
// Eyes (8px wide each)
circ(cx-16, cy-20, 4, 25, 18, 12, eid+'.head.eyes.left');
circ(cx+4, cy-20, 4, 25, 18, 12, eid+'.head.eyes.right');
// Eye shine (white highlight, 4px offset)
px(cx-20, cy-24, 255, 255, 255, eid+'.head.eyes.left');
px(cx, cy-24, 255, 255, 255, eid+'.head.eyes.right');
```

## 4. Fine details (whiskers, noses, claws, moss, cracks, spots)

Add at least 5-10 single-pixel details per character and 3-5 per environment object. \
These are what make pixel art look polished:

```javascript
// Nose (8px, pinkish)
circ(cx-8, cy-8, 4, 200, 130, 140, eid+'.head.nose');
px(cx-4, cy-8, 200, 130, 140, eid+'.head.nose');
// Whiskers
line(cx-28, cy-12, cx-16, cy-12, 130, 100, 40, eid+'.head.whiskers');
line(cx-28, cy-8, cx-16, cy-8, 130, 100, 40, eid+'.head.whiskers');
line(cx+8, cy-12, cx+20, cy-12, 130, 100, 40, eid+'.head.whiskers');
// Moss on rock
for(var i=0;i<5;i++) px(cx-rx+8+i*8, cy-ry+4, 60, 110, 50, eid+'.moss');
```

## 5. Background: sky and ground (MANDATORY, generated by YOU)

You MUST generate the background (sky + ground) as the FIRST sprite code entry \
with entity id `"bg"`. The background MUST be unique to each scene's setting. \
Use gradient loops with Math.sin and Math.random for organic textures. \
The background is drawn first, then entities are drawn on top.

The horizon line position, sky colors, and ground texture must match the scene \
setting (forest, beach, night, cave, city, underwater, etc.).

Example for a **daytime forest**:
```javascript
// Gradient sky
for(var y=0;y<340;y++) for(var x=0;x<PW;x++){
  var g=y/340;
  px(x,y, Math.floor(135+g*20), Math.floor(190+g*26), Math.floor(220+g*20), 'sky');
}
// Textured ground with noise
for(var y=340;y<PH;y++) for(var x=0;x<PW;x++){
  var n=Math.sin(x*0.03+y*0.05)*5+Math.sin(x*0.075)*3;
  var gr=Math.floor(38+n+Math.random()*8);
  px(x,y, Math.floor(gr*0.35), gr, Math.floor(gr*0.2), 'ground');
}
// Grass tufts at horizon
for(var x=0;x<PW;x++){
  var gy=336+Math.floor(Math.sin(x*0.00875)*12);
  for(var dy=0;dy<20;dy++) px(x,gy+dy, 30+Math.floor(Math.random()*12), 55+Math.floor(Math.random()*15), 14, 'ground');
}
```

Example for a **night scene** (dark sky + stars):
```javascript
for(var y=0;y<340;y++) for(var x=0;x<PW;x++){
  var g=y/340;
  px(x,y, Math.floor(5+g*15), Math.floor(8+g*20), Math.floor(22+g*33), 'sky');
}
// Stars
[[60,20],[200,32],[360,12],[560,40],[740,24],[920,16],[1040,32],[140,72],[480,8],[800,60]].forEach(function(s){
  px(s[0],s[1], 255,238,170, 'sky');
});
// Dark ground
for(var y=340;y<PH;y++) for(var x=0;x<PW;x++){
  var n=Math.sin(x*0.03+y*0.05)*4;
  var gr=Math.floor(25+n+Math.random()*6);
  px(x,y, Math.floor(gr*0.3), Math.floor(gr*0.7), Math.floor(gr*0.2), 'ground');
}
```

Example for a **beach**:
```javascript
for(var y=0;y<320;y++) for(var x=0;x<PW;x++){
  var g=y/320;
  px(x,y, Math.floor(100+g*70), Math.floor(180+g*40), Math.floor(240+g*10), 'sky');
}
for(var y=320;y<PH;y++) for(var x=0;x<PW;x++){
  var n=Math.sin(x*0.0375+y*0.0625)*4+Math.random()*6;
  var s=Math.floor(50+n);
  px(x,y, Math.floor(s*0.95), Math.floor(s*0.8), Math.floor(s*0.6), 'ground');
}
```

IMPORTANT: The `bg` sprite code MUST be listed FIRST in the `sprite_code` object \
so it draws before all entities. Vary the sky/ground colors and horizon for every \
different setting. A beach looks nothing like a cave or a city at night.

## 6. Trees: multi-layer canopy

Trees MUST have a trunk + at least 3 layered canopy ellipses (dark→mid→light) \
plus a highlight spot:

```javascript
// Trunk
rect(cx-4, groundY-80, 12, 80, 90, 42, 10, eid+'.trunk');
rect(cx, groundY-80, 4, 80, 105, 57, 25, eid+'.trunk'); // highlight stripe
// Canopy: dark base → mid → light → highlight
ellip(cx, groundY-100, 60, 48, 38, 95, 38, eid+'.canopy');
ellip(cx-20, groundY-92, 32, 32, 50, 125, 50, eid+'.canopy');
ellip(cx+20, groundY-92, 24, 32, 50, 125, 50, eid+'.canopy');
ellip(cx, groundY-120, 40, 32, 62, 145, 62, eid+'.canopy');
circ(cx-20, groundY-120, 16, 77, 160, 77, eid+'.canopy'); // highlight
```

## 7. Scale and positioning

- Characters: 160-280px tall, feet touching ground (y ≈ 400-580).
- Trees: 240-400px tall, trunk base on ground.
- Small objects (mushrooms, flowers, rocks): 64-120px.
- Spread entities across the full 1120px width for composition.
- IMPORTANT: Keep ALL entities fully within the canvas (0,0)-(1119,719). \
  Ensure `center_x - width/2 >= 0` and `center_x + width/2 <= 1119`, and \
  similarly for y. Entities near edges will be forcibly clamped to fit.
- The scene must have DEPTH: place some objects further back (smaller, higher y) \
  and some closer (larger, lower y).

## 8. Night scenes

For night scenes, add stars as scattered single bright pixels on the sky, \
and a moon with 3-4 layered circles plus crater details:

```javascript
// Stars
[[60,20],[200,32],[360,12],[560,40],[740,24],[920,16],[1040,32],[140,72]].forEach(function(s){
  px(s[0],s[1], 255,238,170, 'sky');
});
// Moon (layered glow)
circ(880,100, 60, 255,238,170, 'moon');
circ(880,100, 52, 255,243,205, 'moon');
circ(888,88, 36, 255,250,230, 'moon');
circ(868,80, 8, 235,225,165, 'moon'); // crater
```

## 9. Water bodies (ponds, rivers, ocean)

Water MUST have layered shading + highlight ripple pixels:

```javascript
ellip(cx,cy, rx,ry, 30,90,155, eid);       // deep
ellip(cx,cy, rx-8,ry-4, 45,120,180, eid);  // mid
ellip(cx,cy-4, rx-16,ry-8, 60,150,205, eid); // surface
// Highlights
for(var i=0;i<5;i++){
  px(cx-rx/2+i*20, cy-4, 130,195,235, eid);
}
```

## 10. ENTITY DRAWING BLUEPRINTS (MANDATORY REFERENCE)

You MUST follow these blueprints when drawing common entity types. Each blueprint \
shows the exact technique, proportions, layering, and detail level required. \
Adapt colors and positions, but NEVER simplify the structure. Every entity you \
draw must match or exceed the detail level shown here.

### BLUEPRINT: Rabbit / Bunny

Proportions: body ellipse ~24×20, head circle ~20, ears ~8×24 each. \
Total height ~80-100px. Use 4 fur shades (dark→light). \
Key features: layered body+belly, round head with inner highlight, tall thin ears \
with pink inner, eyes with shine, tiny nose, whisker pixels, round tail, oval feet.

```javascript
const eid = 'rabbit_01';
const cx = 420, cy = 440;
// Body: 3-layer shading
ellip(cx, cy+8, 24, 20, 139,105,20, eid+'.body');        // dark outer
ellip(cx, cy+8, 20, 16, 155,120,35, eid+'.body');         // mid
ellip(cx-8, cy+8, 12, 12, 210,175,115, eid+'.body.belly'); // light belly
// Head: 2-layer
circ(cx-12, cy-20, 20, 155,121,36, eid+'.head');          // outer
circ(cx-12, cy-20, 16, 170,138,55, eid+'.head');           // inner highlight
// Ears: outer + inner pink
ellip(cx-24, cy-56, 8, 24, 145,110,30, eid+'.head.ears.left');
ellip(cx-24, cy-56, 4, 16, 210,175,115, eid+'.head.ears.left');
ellip(cx-4, cy-56, 8, 24, 145,110,30, eid+'.head.ears.right');
ellip(cx-4, cy-56, 4, 16, 210,175,115, eid+'.head.ears.right');
// Eyes: dark + shine pixel
px(cx-20, cy-24, 25,18,12, eid+'.head.eyes.left');
px(cx-16, cy-24, 25,18,12, eid+'.head.eyes.left');
px(cx-4, cy-24, 25,18,12, eid+'.head.eyes.right');
px(cx, cy-24, 25,18,12, eid+'.head.eyes.right');
px(cx-20, cy-28, 255,255,255, eid+'.head.eyes.left');
px(cx-4, cy-28, 255,255,255, eid+'.head.eyes.right');
// Nose
px(cx-12, cy-12, 200,130,140, eid+'.head.nose');
px(cx-8, cy-12, 200,130,140, eid+'.head.nose');
// Whiskers (single pixels extending outward)
px(cx-28, cy-16, 130,100,40, eid+'.head.whiskers');
px(cx-32, cy-20, 130,100,40, eid+'.head.whiskers');
px(cx+4, cy-16, 130,100,40, eid+'.head.whiskers');
px(cx+8, cy-20, 130,100,40, eid+'.head.whiskers');
// Tail: small white-ish puff
circ(cx+24, cy, 8, 220,210,190, eid+'.tail');
// Feet: small ovals at bottom
ellip(cx-12, cy+28, 12, 4, 145,110,30, eid+'.legs.front');
ellip(cx+12, cy+28, 12, 4, 145,110,30, eid+'.legs.back');
```

### BLUEPRINT: Fox

Proportions: body ellipse ~44×24, head circle ~24, legs ~8 rects. \
Total height ~100-120px. Key features: orange-to-cream layering, white chest patch, \
snout ellipse with tiny black nose, TRIANGULAR ears (use `tri`) with inner color, \
4 legs as thin rects + wider paw rects, bushy tail ellipse with white tip.

```javascript
const eid = 'fox_01';
const cx = 720, cy = 460;
// Body: dark→mid layered
ellip(cx, cy, 44, 24, 200,80,48, eid+'.body');
ellip(cx, cy, 36, 20, 215,100,60, eid+'.body');
// Chest/belly: cream patch
ellip(cx-20, cy+4, 16, 16, 235,200,160, eid+'.body.belly');
// Head: 2-layer circle
circ(cx-48, cy-16, 24, 205,85,50, eid+'.head');
circ(cx-48, cy-16, 20, 220,100,60, eid+'.head');
// Snout: cream ellipse + dark nose pixels
ellip(cx-64, cy-8, 12, 8, 235,200,160, eid+'.head.snout');
px(cx-72, cy-12, 35,20,15, eid+'.head.nose');
px(cx-76, cy-12, 35,20,15, eid+'.head.nose');
// Eyes: dark circles + shine
circ(cx-56, cy-24, 4, 15,10,8, eid+'.head.eyes.left');
circ(cx-40, cy-24, 4, 15,10,8, eid+'.head.eyes.right');
px(cx-56, cy-28, 255,255,255, eid+'.head.eyes.left');
px(cx-40, cy-28, 255,255,255, eid+'.head.eyes.right');
// Ears: TRIANGLES (outer + inner lighter triangle)
tri(cx-68,cy-32, cx-64,cy-56, cx-56,cy-32, 200,80,48, eid+'.head.ears.left');
tri(cx-64,cy-36, cx-64,cy-52, cx-56,cy-36, 225,120,80, eid+'.head.ears.left');
tri(cx-40,cy-32, cx-36,cy-56, cx-28,cy-32, 200,80,48, eid+'.head.ears.right');
tri(cx-36,cy-36, cx-36,cy-52, cx-28,cy-36, 225,120,80, eid+'.head.ears.right');
// Legs: 4 thin rects
rect(cx-24, cy+24, 8, 28, 175,65,38, eid+'.legs.front_left');
rect(cx-8, cy+24, 8, 28, 175,65,38, eid+'.legs.front_right');
rect(cx+12, cy+24, 8, 28, 175,65,38, eid+'.legs.back_left');
rect(cx+28, cy+24, 8, 28, 175,65,38, eid+'.legs.back_right');
// Paws: wider rects at foot
rect(cx-28, cy+48, 16, 8, 200,80,48, eid+'.legs.front_left');
rect(cx-12, cy+48, 16, 8, 200,80,48, eid+'.legs.front_right');
rect(cx+8, cy+48, 16, 8, 200,80,48, eid+'.legs.back_left');
rect(cx+24, cy+48, 16, 8, 200,80,48, eid+'.legs.back_right');
// Tail: bushy ellipse + white tip
ellip(cx+56, cy-8, 28, 12, 200,80,48, eid+'.tail');
ellip(cx+72, cy-12, 12, 8, 240,210,170, eid+'.tail');
```

### BLUEPRINT: Cat

Proportions: body ellipse ~40×24, head circle ~24, pointed ears as triangles. \
Key features: sleek body with 3-shade fur, triangular ears with inner pink, \
almond-shaped eyes (horizontal ellipses) with vertical-slit pupils, tiny pink nose, \
whiskers extending far, curved tail using multiple ellipses/circles, small neat paws.

```javascript
const eid = 'cat_01';
const cx = 560, cy = 460;
// Body
ellip(cx, cy, 40, 24, 100,100,105, eid+'.body');        // dark grey
ellip(cx, cy, 32, 20, 130,130,135, eid+'.body');         // mid grey
ellip(cx-12, cy+4, 16, 12, 170,170,175, eid+'.body.belly'); // light belly
// Head
circ(cx-40, cy-12, 24, 120,120,125, eid+'.head');
circ(cx-40, cy-12, 20, 140,140,145, eid+'.head');
// Ears: pointed triangles
tri(cx-60,cy-28, cx-56,cy-52, cx-48,cy-28, 120,120,125, eid+'.head.ears.left');
tri(cx-56,cy-32, cx-56,cy-48, cx-48,cy-32, 180,140,145, eid+'.head.ears.left');
tri(cx-32,cy-28, cx-28,cy-52, cx-20,cy-28, 120,120,125, eid+'.head.ears.right');
tri(cx-28,cy-32, cx-28,cy-48, cx-20,cy-32, 180,140,145, eid+'.head.ears.right');
// Eyes: almond shape (wider ellipses) with slit pupil
ellip(cx-52, cy-16, 8, 4, 180,200,60, eid+'.head.eyes.left');   // yellow-green iris
ellip(cx-28, cy-16, 8, 4, 180,200,60, eid+'.head.eyes.right');
px(cx-52, cy-16, 15,10,8, eid+'.head.eyes.left');  // slit pupil
px(cx-28, cy-16, 15,10,8, eid+'.head.eyes.right');
px(cx-56, cy-20, 255,255,255, eid+'.head.eyes.left');  // shine
px(cx-32, cy-20, 255,255,255, eid+'.head.eyes.right');
// Nose: tiny pink triangle
px(cx-40, cy-4, 200,130,140, eid+'.head.nose');
px(cx-44, cy-4, 200,130,140, eid+'.head.nose');
// Whiskers: long lines extending outward
line(cx-64, cy-8, cx-48, cy-8, 160,160,165, eid+'.head.whiskers');
line(cx-64, cy-4, cx-48, cy-4, 160,160,165, eid+'.head.whiskers');
line(cx-16, cy-8, cx, cy-8, 160,160,165, eid+'.head.whiskers');
line(cx-16, cy-4, cx, cy-4, 160,160,165, eid+'.head.whiskers');
// Legs
rect(cx-20, cy+24, 8, 24, 110,110,115, eid+'.legs.front_left');
rect(cx-4, cy+24, 8, 24, 110,110,115, eid+'.legs.front_right');
rect(cx+16, cy+24, 8, 24, 110,110,115, eid+'.legs.back_left');
rect(cx+32, cy+24, 8, 24, 110,110,115, eid+'.legs.back_right');
// Paws
ellip(cx-16, cy+48, 8, 4, 135,135,140, eid+'.legs.front_left');
ellip(cx, cy+48, 8, 4, 135,135,140, eid+'.legs.front_right');
ellip(cx+20, cy+48, 8, 4, 135,135,140, eid+'.legs.back_left');
ellip(cx+36, cy+48, 8, 4, 135,135,140, eid+'.legs.back_right');
// Tail: curved using overlapping circles
circ(cx+44, cy-4, 8, 120,120,125, eid+'.tail');
circ(cx+52, cy-12, 8, 120,120,125, eid+'.tail');
circ(cx+56, cy-24, 8, 120,120,125, eid+'.tail');
circ(cx+56, cy-36, 4, 130,130,135, eid+'.tail');
```

### BLUEPRINT: Bird (perching)

Proportions: round body ~28×20, small round head ~16, triangle beak, \
single wing on side, thin legs, fan tail. Very compact. \
Key features: round chubby body, contrasting breast color, pointed beak, \
dot eye with shine, wing as overlapping ellipses, stick legs, spread tail feathers.

```javascript
const eid = 'bird_01';
const cx = 600, cy = 400;
// Body: round, chubby
ellip(cx, cy, 28, 20, 55,90,160, eid+'.body');          // blue-ish
ellip(cx, cy, 24, 16, 70,110,180, eid+'.body');
ellip(cx-4, cy+4, 12, 12, 210,180,140, eid+'.body.breast'); // orange breast
// Head
circ(cx-24, cy-16, 16, 65,100,170, eid+'.head');
circ(cx-24, cy-16, 12, 80,115,185, eid+'.head');
// Eye
circ(cx-28, cy-20, 4, 15,10,10, eid+'.head.eyes.left');
px(cx-32, cy-24, 255,255,255, eid+'.head.eyes.left');
// Beak: small triangle
tri(cx-40, cy-16, cx-32, cy-12, cx-32, cy-20, 240,180,60, eid+'.head.beak');
// Wing: overlapping ellipse on body side
ellip(cx+8, cy-4, 20, 12, 50,80,150, eid+'.body.wing');
ellip(cx+8, cy-4, 16, 8, 60,95,165, eid+'.body.wing');
// Tail feathers: fan of lines
line(cx+28, cy, cx+44, cy-8, 50,80,150, eid+'.tail');
line(cx+28, cy+4, cx+44, cy+4, 50,80,150, eid+'.tail');
line(cx+28, cy+8, cx+44, cy+16, 50,80,150, eid+'.tail');
// Legs: thin lines
line(cx-8, cy+20, cx-12, cy+36, 80,60,40, eid+'.legs.left');
line(cx+8, cy+20, cx+4, cy+36, 80,60,40, eid+'.legs.right');
// Feet: small toes
px(cx-16, cy+36, 80,60,40, eid+'.legs.left');
px(cx-8, cy+36, 80,60,40, eid+'.legs.left');
px(cx, cy+36, 80,60,40, eid+'.legs.right');
px(cx+8, cy+36, 80,60,40, eid+'.legs.right');
```

### BLUEPRINT: Fish

Proportions: body ellipse ~40×16 (flat, wide), triangular tail, small fins, \
eye on one side. Key features: layered body with shimmer gradient, \
crescent tail using 2 triangles, dorsal fin triangle on top, pectoral fin below, \
scales as scattered highlight pixels, mouth as 1-2 dark pixels.

```javascript
const eid = 'fish_01';
const cx = 400, cy = 500;
// Body: 3-layer ellipse (flat/wide)
ellip(cx, cy, 40, 16, 220,120,40, eid+'.body');            // dark
ellip(cx, cy, 32, 12, 240,150,60, eid+'.body');             // mid
ellip(cx-8, cy+4, 16, 8, 255,200,100, eid+'.body.belly');   // light belly
// Tail fin: 2 triangles forming V
tri(cx+40, cy, cx+60, cy-16, cx+44, cy, 220,100,30, eid+'.tail');
tri(cx+40, cy, cx+60, cy+16, cx+44, cy, 220,100,30, eid+'.tail');
// Dorsal fin: triangle on top
tri(cx-8, cy-16, cx+12, cy-28, cx+16, cy-16, 200,90,25, eid+'.body.fin_dorsal');
// Pectoral fin: small triangle below
tri(cx-12, cy+8, cx-20, cy+20, cx-4, cy+12, 210,110,35, eid+'.body.fin_pectoral');
// Eye
circ(cx-24, cy-4, 8, 255,255,255, eid+'.head.eyes.left');
circ(cx-24, cy-4, 4, 15,10,8, eid+'.head.eyes.left');
px(cx-28, cy-8, 255,255,255, eid+'.head.eyes.left');
// Mouth
px(cx-40, cy, 160,60,20, eid+'.head.mouth');
// Scales: highlight shimmer pixels scattered on body
for(var i=0;i<6;i++) px(cx-16+i*12, cy-4+((i%2)*4), 255,220,140, eid+'.body');
```

### BLUEPRINT: Crab

Proportions: wide flat body ellipse ~40×20, 2 large claws as circles+ellipses, \
6 thin legs (3 per side), 2 stalked eyes on top. \
Key features: WIDE body (wider than tall), 2 distinct claws with pincers \
(each claw = arm ellipse + 2 pincer arcs/triangles), thin segmented legs \
spreading outward, eyes on stalks above body.

```javascript
const eid = 'crab_01';
const cx = 500, cy = 480;
// Body: wide flat ellipse, layered
ellip(cx, cy, 40, 20, 180,50,40, eid+'.body');            // dark shell
ellip(cx, cy, 32, 16, 210,70,55, eid+'.body');             // mid
ellip(cx, cy-4, 20, 8, 230,100,75, eid+'.body');           // highlight
// Eye stalks: thin rects going up from body
rect(cx-16, cy-28, 4, 12, 180,50,40, eid+'.head.eyestalk.left');
rect(cx+12, cy-28, 4, 12, 180,50,40, eid+'.head.eyestalk.right');
// Eyes: small circles on top of stalks
circ(cx-16, cy-32, 8, 20,20,20, eid+'.head.eyes.left');
circ(cx+16, cy-32, 8, 20,20,20, eid+'.head.eyes.right');
px(cx-16, cy-36, 255,255,255, eid+'.head.eyes.left');
px(cx+12, cy-36, 255,255,255, eid+'.head.eyes.right');
// LEFT CLAW: arm + pincer
ellip(cx-52, cy-8, 12, 8, 200,60,48, eid+'.claws.left');         // arm
ellip(cx-68, cy-12, 12, 8, 220,80,60, eid+'.claws.left');        // claw base
// Pincer: 2 small triangles forming open pincer shape
tri(cx-76, cy-20, cx-68, cy-12, cx-60, cy-20, 220,80,60, eid+'.claws.left');
tri(cx-76, cy-4, cx-68, cy-12, cx-60, cy-4, 210,70,55, eid+'.claws.left');
// RIGHT CLAW: mirror
ellip(cx+52, cy-8, 12, 8, 200,60,48, eid+'.claws.right');
ellip(cx+68, cy-12, 12, 8, 220,80,60, eid+'.claws.right');
tri(cx+60, cy-20, cx+68, cy-12, cx+76, cy-20, 220,80,60, eid+'.claws.right');
tri(cx+60, cy-4, cx+68, cy-12, cx+76, cy-4, 210,70,55, eid+'.claws.right');
// Legs: 3 per side, angled outward, using lines
line(cx-28, cy+12, cx-48, cy+28, 175,50,38, eid+'.legs.left_1');
line(cx-24, cy+16, cx-40, cy+36, 175,50,38, eid+'.legs.left_2');
line(cx-20, cy+20, cx-32, cy+40, 175,50,38, eid+'.legs.left_3');
line(cx+28, cy+12, cx+48, cy+28, 175,50,38, eid+'.legs.right_1');
line(cx+24, cy+16, cx+40, cy+36, 175,50,38, eid+'.legs.right_2');
line(cx+20, cy+20, cx+32, cy+40, 175,50,38, eid+'.legs.right_3');
// Leg tips: small pixels at end of each leg
px(cx-48, cy+28, 190,65,48, eid+'.legs.left_1');
px(cx-40, cy+36, 190,65,48, eid+'.legs.left_2');
px(cx-32, cy+40, 190,65,48, eid+'.legs.left_3');
px(cx+48, cy+28, 190,65,48, eid+'.legs.right_1');
px(cx+40, cy+36, 190,65,48, eid+'.legs.right_2');
px(cx+32, cy+40, 190,65,48, eid+'.legs.right_3');
```

### BLUEPRINT: Frog

Proportions: wide squat body ~36×20, big head ~28 radius, huge bulging eyes, \
wide mouth line, short bent legs. \
Key features: smooth green layered body, lighter belly, very large protruding eyes \
(circles that extend above head line), wide smiling mouth arc, webbed feet.

```javascript
const eid = 'frog_01';
const cx = 400, cy = 480;
// Body: squat and wide
ellip(cx, cy, 36, 20, 40,120,35, eid+'.body');           // dark green
ellip(cx, cy, 28, 16, 55,150,45, eid+'.body');            // mid green
ellip(cx, cy+4, 20, 12, 120,190,80, eid+'.body.belly');   // light belly
// Head merged with body (wider ellipse on top)
ellip(cx, cy-12, 32, 16, 50,140,40, eid+'.head');
ellip(cx, cy-12, 24, 12, 65,160,55, eid+'.head');
// Eyes: large, protruding ABOVE head
circ(cx-20, cy-32, 12, 50,140,40, eid+'.head.eyes.left');
circ(cx-20, cy-32, 12, 240,240,220, eid+'.head.eyes.left');   // white
circ(cx-20, cy-32, 8, 15,15,10, eid+'.head.eyes.left');       // pupil
px(cx-24, cy-36, 255,255,255, eid+'.head.eyes.left');          // shine
circ(cx+20, cy-32, 12, 50,140,40, eid+'.head.eyes.right');
circ(cx+20, cy-32, 12, 240,240,220, eid+'.head.eyes.right');
circ(cx+20, cy-32, 8, 15,15,10, eid+'.head.eyes.right');
px(cx+16, cy-36, 255,255,255, eid+'.head.eyes.right');
// Mouth: wide arc
arc(cx, cy-4, 20, 0.1, 3.04, 30,80,25, eid+'.head.mouth');
// Front legs: short, bent
rect(cx-28, cy+12, 8, 16, 45,130,38, eid+'.legs.front_left');
rect(cx+20, cy+12, 8, 16, 45,130,38, eid+'.legs.front_right');
// Back legs: larger, bent (2 rects for thigh + shin)
rect(cx-40, cy+4, 12, 16, 45,130,38, eid+'.legs.back_left');
rect(cx-44, cy+16, 8, 16, 40,120,35, eid+'.legs.back_left');
rect(cx+28, cy+4, 12, 16, 45,130,38, eid+'.legs.back_right');
rect(cx+36, cy+16, 8, 16, 40,120,35, eid+'.legs.back_right');
// Webbed feet: small spread shapes
ellip(cx-48, cy+32, 12, 4, 50,140,40, eid+'.legs.back_left');
ellip(cx+40, cy+32, 12, 4, 50,140,40, eid+'.legs.back_right');
// Skin spots/texture
px(cx-12, cy-4, 40,110,30, eid+'.body');
px(cx+8, cy+4, 40,110,30, eid+'.body');
px(cx-16, cy+8, 40,110,30, eid+'.body');
```

### BLUEPRINT: Turtle / Tortoise

Proportions: domed shell ~48×28 (tall ellipse), small head poking out ~16 radius, \
4 stubby legs. Key features: shell with layered dome + hexagonal pattern (drawn as \
scattered darker patches on shell), head with tiny beak-like mouth, wrinkled texture.

```javascript
const eid = 'turtle_01';
const cx = 600, cy = 480;
// Shell: domed, layered
ellip(cx, cy-4, 48, 28, 80,100,50, eid+'.body.shell');       // dark
ellip(cx, cy-8, 40, 24, 100,130,60, eid+'.body.shell');      // mid
ellip(cx, cy-12, 28, 16, 120,155,75, eid+'.body.shell');     // highlight
// Shell pattern: darker hexagonal patches
circ(cx-16, cy-12, 8, 75,95,45, eid+'.body.shell');
circ(cx+12, cy-8, 8, 75,95,45, eid+'.body.shell');
circ(cx, cy-20, 8, 75,95,45, eid+'.body.shell');
circ(cx-8, cy, 8, 75,95,45, eid+'.body.shell');
circ(cx+20, cy-16, 8, 75,95,45, eid+'.body.shell');
// Head: poking out left
ellip(cx-52, cy+4, 16, 12, 90,120,55, eid+'.head');
ellip(cx-52, cy+4, 12, 8, 110,145,70, eid+'.head');
// Eye
circ(cx-60, cy-4, 4, 15,12,8, eid+'.head.eyes.left');
px(cx-64, cy-8, 255,255,255, eid+'.head.eyes.left');
// Mouth line
px(cx-68, cy+8, 60,80,40, eid+'.head.mouth');
px(cx-68, cy+8, 60,80,40, eid+'.head.mouth');
// Legs: 4 stubby
ellip(cx-32, cy+24, 12, 8, 90,120,55, eid+'.legs.front_left');
ellip(cx-8, cy+24, 12, 8, 90,120,55, eid+'.legs.front_right');
ellip(cx+16, cy+24, 12, 8, 90,120,55, eid+'.legs.back_left');
ellip(cx+36, cy+24, 12, 8, 90,120,55, eid+'.legs.back_right');
// Tail: tiny nub
circ(cx+48, cy+8, 4, 90,120,55, eid+'.tail');
// Shell edge highlight
arc(cx, cy-4, 48, 3.5, 5.8, 130,165,85, eid+'.body.shell');
```

### BLUEPRINT: Snowman

Proportions: 3 stacked circles (bottom ~40, middle ~28, head ~20). \
Key features: 3 layered white-to-grey circles, coal eyes + mouth dots, \
carrot nose (orange triangle), stick arms (thick lines), top hat or scarf, \
button details on middle section.

```javascript
const eid = 'snowman_01';
const cx = 400, cy = 440;
// Bottom ball
circ(cx, cy+32, 40, 210,215,225, eid+'.body.bottom');
circ(cx, cy+32, 36, 225,230,240, eid+'.body.bottom');
circ(cx-8, cy+40, 16, 240,242,248, eid+'.body.bottom');  // snow highlight
// Middle ball
circ(cx, cy-8, 28, 215,220,230, eid+'.body.middle');
circ(cx, cy-8, 24, 230,234,242, eid+'.body.middle');
// Head
circ(cx, cy-44, 20, 218,222,232, eid+'.head');
circ(cx, cy-44, 16, 235,238,245, eid+'.head');
// Eyes: coal
circ(cx-8, cy-48, 4, 20,20,25, eid+'.head.eyes.left');
circ(cx+8, cy-48, 4, 20,20,25, eid+'.head.eyes.right');
px(cx-12, cy-52, 255,255,255, eid+'.head.eyes.left');
px(cx+4, cy-52, 255,255,255, eid+'.head.eyes.right');
// Carrot nose
tri(cx, cy-40, cx+20, cy-36, cx, cy-32, 240,140,40, eid+'.head.nose');
// Mouth: coal dots in smile arc
px(cx-12, cy-32, 25,20,20, eid+'.head.mouth');
px(cx-8, cy-28, 25,20,20, eid+'.head.mouth');
px(cx, cy-28, 25,20,20, eid+'.head.mouth');
px(cx+8, cy-28, 25,20,20, eid+'.head.mouth');
px(cx+12, cy-32, 25,20,20, eid+'.head.mouth');
// Buttons on middle
circ(cx, cy-16, 4, 25,20,20, eid+'.body.middle');
circ(cx, cy, 4, 25,20,20, eid+'.body.middle');
// Stick arms
thickLine(cx-28, cy-12, cx-60, cy-32, 4, 80,50,20, eid+'.body.arm_left');
thickLine(cx+28, cy-12, cx+60, cy-32, 4, 80,50,20, eid+'.body.arm_right');
// Twig fingers
px(cx-60, cy-36, 80,50,20, eid+'.body.arm_left');
px(cx-64, cy-32, 80,50,20, eid+'.body.arm_left');
px(cx+60, cy-36, 80,50,20, eid+'.body.arm_right');
px(cx+64, cy-32, 80,50,20, eid+'.body.arm_right');
// Scarf
rect(cx-20, cy-28, 40, 8, 200,40,40, eid+'.body.scarf');
rect(cx+16, cy-28, 8, 24, 200,40,40, eid+'.body.scarf');
```

### BLUEPRINT: Dog

Similar to fox but stockier body, floppy ears (ellipses hanging DOWN), \
shorter snout, wagging tail as curved upward shape. Use brown/golden tones. \
Must have: 3-layer body, floppy ear ellipses, wide happy mouth, tongue pixel(s), \
thick wagging tail curving up, collar rect with tag circle.

### BLUEPRINT: Butterfly

Body as tiny rect ~2×8, 4 wings as overlapping colorful ellipses (2 big upper, \
2 small lower), antennae as lines with dot tips. Wings should have pattern pixels \
(spots/dots). Very colorful — use contrasting brights.

### BLUEPRINT: Mushroom

```javascript
const eid = 'mush_01';
const cx = 300, groundY = 520;
// Stem: 2-layer rect
rect(cx-4, groundY-32, 12, 32, 200,195,155, eid+'.stem');
rect(cx, groundY-32, 4, 32, 220,215,175, eid+'.stem');  // highlight
// Cap: 2-layer dome (ellipse)
ellip(cx, groundY-36, 20, 16, 204,50,50, eid+'.cap');
ellip(cx, groundY-44, 16, 12, 224,70,70, eid+'.cap');   // highlight
// Spots: white dots on cap
px(cx-12, groundY-40, 255,230,220, eid+'.cap');
px(cx+8, groundY-44, 255,230,220, eid+'.cap');
px(cx-4, groundY-48, 255,230,220, eid+'.cap');
// Cap underside: lighter fringe
for(var i=-16;i<=16;i++) px(cx+i, groundY-20, 220,200,170, eid+'.cap');
```

### BLUEPRINT: Rock

```javascript
const eid = 'rock_01';
const cx = 600, cy = 500;
var rx = 40, ry = 24;
// Layered ellipses (dark → mid → light)
ellip(cx, cy, rx, ry, 80,80,80, eid);
ellip(cx, cy-4, rx-4, ry-4, 100,98,95, eid);
ellip(cx, cy-8, rx-12, ry-8, 118,115,110, eid);
// Cracks: dark lines across surface
for(var i=0;i<rx;i+=12) px(cx-rx/2+i, cy, 60,58,55, eid);
// Moss: green pixels on top edge
for(var i=0;i<4;i++) px(cx-rx+8+i*8, cy-ry+4, 60,110,50, eid+'.moss');
// Pebble detail: scattered lighter pixels
px(cx+8, cy-8, 130,128,122, eid);
px(cx-12, cy+4, 125,122,118, eid);
```

### BLUEPRINT: Flower

```javascript
const eid = 'flower_01';
const cx = 360, groundY = 520;
// Stem
rect(cx, groundY-36, 4, 36, 50,110,40, eid+'.stem');
// Leaf
ellip(cx+8, groundY-16, 8, 4, 55,130,45, eid+'.stem.leaf');
// Petals: 5 small circles around center
var petalR = 8;
circ(cx, groundY-44, petalR, 255,100,120, eid+'.petals');     // top
circ(cx-12, groundY-36, petalR, 240,90,110, eid+'.petals');   // left
circ(cx+12, groundY-36, petalR, 240,90,110, eid+'.petals');   // right
circ(cx-8, groundY-28, petalR, 230,85,105, eid+'.petals');    // bottom-left
circ(cx+8, groundY-28, petalR, 230,85,105, eid+'.petals');    // bottom-right
// Center
circ(cx, groundY-36, 8, 255,220,60, eid+'.center');
px(cx-4, groundY-40, 255,240,100, eid+'.center');  // pollen highlight
```

### BLUEPRINT: House / Cottage

Proportions: rect body ~100×80, triangle roof ~120 wide, door rect, \
window squares with cross-bars, chimney rect with smoke. \
Key features: 2-shade brick/wood walls, darker roof with highlight edge, \
windows with inner glow, door with knob pixel, chimney + wispy smoke pixels.

```javascript
const eid = 'house_01';
const cx = 600, groundY = 520;
// Walls
rect(cx-52, groundY-80, 100, 80, 140,110,75, eid+'.walls');
rect(cx-48, groundY-76, 92, 72, 160,130,90, eid+'.walls');
// Roof: triangle
tri(cx-60, groundY-80, cx, groundY-140, cx+60, groundY-80, 150,50,40, eid+'.roof');
tri(cx-52, groundY-80, cx, groundY-132, cx+52, groundY-80, 170,65,50, eid+'.roof');
// Roof edge highlight
line(cx-60, groundY-80, cx, groundY-140, 180,80,65, eid+'.roof');
// Door
rect(cx-12, groundY-48, 24, 48, 100,60,30, eid+'.door');
rect(cx-8, groundY-44, 16, 40, 120,75,40, eid+'.door');
px(cx+4, groundY-24, 200,180,50, eid+'.door');  // knob
// Windows: left and right with glow
rect(cx-40, groundY-64, 20, 20, 80,50,25, eid+'.windows.left');
rect(cx-36, groundY-60, 12, 12, 240,220,140, eid+'.windows.left');  // warm glow
px(cx-32, groundY-56, 200,180,80, eid+'.windows.left');   // cross
rect(cx+20, groundY-64, 20, 20, 80,50,25, eid+'.windows.right');
rect(cx+24, groundY-60, 12, 12, 240,220,140, eid+'.windows.right');
px(cx+32, groundY-56, 200,180,80, eid+'.windows.right');
// Chimney
rect(cx+32, groundY-124, 16, 44, 130,90,65, eid+'.chimney');
rect(cx+36, groundY-124, 8, 44, 145,100,72, eid+'.chimney');
// Smoke wisps
px(cx+40, groundY-132, 180,180,185, eid+'.chimney.smoke');
px(cx+44, groundY-140, 170,170,175, eid+'.chimney.smoke');
px(cx+36, groundY-148, 160,160,168, eid+'.chimney.smoke');
```

### BLUEPRINT: Cloud

```javascript
const eid = 'cloud_01';
const cx = 300, cy = 100;
// Overlapping circles forming fluffy cloud
circ(cx, cy, 28, 220,225,235, eid);
circ(cx-28, cy+8, 20, 215,220,232, eid);
circ(cx+28, cy+8, 20, 215,220,232, eid);
circ(cx-16, cy-8, 20, 230,234,242, eid);     // top highlight
circ(cx+16, cy-8, 20, 230,234,242, eid);
circ(cx, cy-12, 16, 240,243,250, eid);       // bright top center
// Bottom flattening: lighter fringe
ellip(cx, cy+16, 36, 8, 228,232,240, eid);
```

### BLUEPRINT: Sun

```javascript
const eid = 'sun_01';
const cx = 120, cy = 80;
// Glow halo (large, faint)
circ(cx, cy, 48, 255,240,180, eid+'.glow');
// Core: layered
circ(cx, cy, 32, 255,210,80, eid);
circ(cx, cy, 24, 255,230,110, eid);
circ(cx, cy, 12, 255,245,180, eid);  // bright center
// Rays: lines extending outward
for(var a=0; a<8; a++){
  var angle = a * Math.PI/4;
  var x2 = Math.round(cx + Math.cos(angle)*56);
  var y2 = Math.round(cy + Math.sin(angle)*56);
  line(Math.round(cx+Math.cos(angle)*36), Math.round(cy+Math.sin(angle)*36), x2, y2, 255,220,100, eid+'.rays');
}
```

### General rules for ANY entity not listed above

When drawing an entity type not covered by these blueprints:

1. **Research the silhouette**: What makes this animal/object instantly recognizable? \
   (e.g., elephant = large body + trunk + big ears, octopus = round body + 8 tentacles)
2. **Build from core shapes**: Start with the largest body part (ellipse), add head, \
   then distinctive features as separate sub-entities.
3. **ALWAYS use 3-layer shading** on every major shape.
4. **ALWAYS include eyes with shine** for any creature.
5. **Add 5-10 detail pixels**: texture dots, spots, stripes, whiskers, claws, etc.
6. **Use at least 8 distinct sub-entity IDs** per entity.
7. **Proportions matter**: reference real anatomy. A crab is WIDER than tall. \
   A snake is VERY long and thin. A bear is stocky and massive. \
   An owl has a round face with huge forward-facing eyes.

## Summary checklist for EVERY sprite code block:
- [ ] `"bg"` key is FIRST in sprite_code: gradient sky + textured ground matching setting
- [ ] Every shape: 2-3 layers dark→light (NO flat single-color fills)
- [ ] Characters: body shading + eye detail (pupil+shine) + nose + fine details
- [ ] Trees: trunk highlight + 3+ canopy layers + highlight spot
- [ ] Single-pixel details: whiskers, moss, cracks, spots, reflections
- [ ] Cohesive palette: 3-4 shades per material, warm natural tones
- [ ] Proper depth: entities spread across canvas, varied sizes
- [ ] Characters 160-280px tall, feet near y=400-580 on the ground
- [ ] Follow the entity blueprint if one exists for this entity type
- [ ] At least 8 distinct sub-entity IDs per entity

# Sprite code: only NEW entities

Generate sprite_code ONLY for entities with `carried_over: false`. \
Entities with `carried_over: true` reuse their existing code from story_state. \
The `carried_over_entities` array lists entity IDs that persist from previous scenes.

For the first scene (no previous story_state), all entities are new — \
`carried_over` is false for all, `carried_over_entities` is empty.

# NEG self-check

After building the manifest and NEG, perform this internal verification:

1. For each SKILL objective in the session, check that at least one NEG target \
   exercises it. The objectives and their associated error types are:
   - **descriptive_adjectives** → PROPERTY_COLOR, PROPERTY_SIZE, PROPERTY_WEIGHT, PROPERTY_TEMPERATURE, PROPERTY_STATE
   - **spatial_prepositions** → SPATIAL, RELATIONAL
   - **temporal_sequences** → TEMPORAL
   - **quantity** → QUANTITY
   - **action_verbs** → ACTION, MANNER

2. If a SKILL objective is NOT covered by any target, ENRICH the scene:
   - Add an entity or relation that creates a narration opportunity for that objective.
   - Add a corresponding NEG target.
   - Generate sprite code for any added entity.

3. Set `skill_coverage_check` to "PASS" only after all objectives are covered. \
   If you cannot cover an objective, set it to "PARTIAL" and explain in the \
   narrative_text.

# Error exclusion rules

For each entity, exclude impossible error types:
- Entity is unique in the scene → exclude QUANTITY
- Entity has no distinctive color → exclude PROPERTY_COLOR
- Entity is static (no action) → exclude MANNER, ACTION
- Entity has no weight property → exclude PROPERTY_WEIGHT
- Entity has no temperature property → exclude PROPERTY_TEMPERATURE
- Entity has no spatial relation → exclude SPATIAL
- Background/decoration entity → exclude IDENTITY
"""

INITIAL_SCENE_USER_PROMPT = """\
Generate an opening scene for a new story. This is for the story selection page \
where the child picks from 3 options.

SKILL objectives for this session: {skill_objectives}

Seed index: {seed_index} (use this to vary the theme — different characters, \
settings, and moods for each seed).

Requirements:
- Create a fresh, imaginative scene with 1 main character and 2-3 environment elements.
- The character should have a clear personality and distinctive visual features.
- Include a narrative hook that makes the child want to tell this story.
- All entities are new (carried_over: false, carried_over_entities: []).
- background_changed: true (initial scene, always needs a new background).
- Scene ID: "scene_01".

Vary based on seed_index:
- seed 1: forest/nature theme
- seed 2: ocean/beach theme
- seed 3: city/town theme
- other seeds: surprise me with an unusual setting
"""

CONTINUATION_SCENE_USER_PROMPT = """\
Generate the next scene in an ongoing story.

# Story so far
{story_context}

# Previous scene manifest
{previous_manifest}

# Active entities (with existing sprite code)
{active_entities}

{student_profile_context}

# SKILL objectives
{skill_objectives}

# Instructions
- Continue the narrative naturally from where it left off.
- Keep existing characters (mark them carried_over: true). You may introduce 1-2 new entities.
- Generate sprite_code ONLY for new entities (carried_over: false).
- List all persisting entity IDs in carried_over_entities.
- Set background_changed: false if the scene stays in the same location/setting/time of \
day as the previous scene. Set true if the scene moves to a new place or time changes.
- Adapt the scene complexity based on the student profile:
  - If the child struggles with a skill area, create more opportunities for that area.
  - If the child is strong in an area, maintain but don't over-emphasize it.
- Advance the plot — something new should happen.
- Scene ID: "scene_{scene_number:02d}".
"""

BRANCH_DIRECTIVE = """\

# Branch generation context

You are generating branch {branch_index} of {total_branches} candidate \
next scenes. The child will choose ONE of these branches to continue the story.

Each branch MUST offer a DISTINCT narrative direction. Follow this guidance \
for branch {branch_index}:
{branch_flavor}

Also include a "preview_entities" array listing the IDs of the 1-2 most \
visually interesting NEW entities in this branch (used for thumbnail preview).

{profile_emphasis}
"""
