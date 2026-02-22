"""Prompts for the standalone scene image generation module.

Generates HD backgrounds and individual element images for compositing.
Model: gemini-3-pro-image-preview.

All prompts share a STYLE_PREAMBLE to ensure visual coherence between
background and elements within a scene.
"""

# ---------------------------------------------------------------------------
# Shared style preamble — injected into every image prompt for coherence
# ---------------------------------------------------------------------------

STYLE_PREAMBLE = """\
## Visual Style (MANDATORY — apply to ALL images)
- **Children's book illustration**: warm, friendly, inviting — suitable for ages 7-11.
- **Soft watercolor / gouache feel** with gentle outlines and rounded shapes.
- **Consistent color palette**: pastel-leaning tones with rich but not saturated colors.
- **Consistent lighting**: warm natural light from the upper-left.
- **Flat 2D side-view** (like a storybook illustration): no 3D perspective,
  no vanishing points. Objects farther away are higher and smaller, not perspectively foreshortened.
- **Consistent line weight**: thin, soft outlines (~1-2 px at final resolution).
- **No text, no UI, no watermarks.**
"""

# ---------------------------------------------------------------------------
# Background image prompt
# ---------------------------------------------------------------------------

IMAGE_BG_PROMPT_TEMPLATE = """\
Create a HIGH DEFINITION background illustration for a children's story scene.
This is ONLY the environment — **absolutely no characters, no interactive objects,
no animals, no people**. Just the landscape / interior / setting.

## Scene Environment
{scene_description}

## Ground
- Ground type: {ground_type}
- Horizon line at approximately {horizon_pct}% from the top of the image.
- Sky / ceiling above, ground / floor below.

{style_preamble}

## Background-Specific Rules
- **Atmospheric depth**: use subtle color gradients to suggest depth
  (lighter / hazier tones in the distance, richer tones in the foreground).
- **Rich texture**: the ground should have visible texture (grass blades,
  pebbles, wood grain, sand ripples — whatever fits the ground type).
- **Atmospheric details**: clouds, sun glow, distant hills, ambient particles, etc.
- **Leave space for characters**: the main action area (roughly the lower 60%
  of the image) should not be cluttered with non-removable details.
- **16:9 aspect ratio** (landscape orientation).
- **Resolution**: produce the highest resolution the model supports.
"""

# ---------------------------------------------------------------------------
# Element (character / object) image prompt
# ---------------------------------------------------------------------------

IMAGE_ELEMENT_PROMPT_TEMPLATE = """\
Create an illustration of a single character or object for a children's story.
The subject must be on a **perfectly solid bright green (#00FF00) background**.
The green must be uniform — no gradients, no shading, no variation.

## Subject
{element_description}

## Pose & Orientation
- Orientation: {orientation} (the character/object faces this direction).
- Relative size: {relative_size}.
- The subject should fill most of the image — center it, leave only a small
  margin of green around it.

{style_preamble}

## Element-Specific Rules
- **The subject must match the style of this background scene**: {scene_style_hint}
- **NO other elements**: no ground, no shadow, no other characters, no decorations.
  ONLY the single subject on solid green (#00FF00).
- **The subject must NOT contain any bright green (#00FF00) pixels.**
  If the subject is naturally green (frog, leaf, etc.), use a slightly
  different green (e.g., #228B22, #2E8B57) so it is distinguishable
  from the chroma-key background.
- **1:1 aspect ratio** (square image).
- **Consistent perspective**: same flat 2D side-view as the background.
"""

# ---------------------------------------------------------------------------
# Helpers to build final prompts from manifest data
# ---------------------------------------------------------------------------


def build_background_prompt(
    scene_description: str,
    ground_type: str = "herbe",
    horizon_line: float = 0.6,
) -> str:
    """Build the full background generation prompt."""
    return IMAGE_BG_PROMPT_TEMPLATE.format(
        scene_description=scene_description,
        ground_type=ground_type,
        horizon_pct=int(horizon_line * 100),
        style_preamble=STYLE_PREAMBLE,
    )


def build_element_prompt(
    element_name: str,
    element_type: str,
    orientation: str = "face_right",
    relative_size: str = "medium",
    scene_description: str = "",
    extra_description: str = "",
) -> str:
    """Build the full element generation prompt."""
    desc_parts = [f"A {relative_size} {element_type} named '{element_name}'."]
    if extra_description:
        desc_parts.append(extra_description)

    return IMAGE_ELEMENT_PROMPT_TEMPLATE.format(
        element_description=" ".join(desc_parts),
        orientation=orientation,
        relative_size=relative_size,
        scene_style_hint=scene_description or "(children's storybook style)",
        style_preamble=STYLE_PREAMBLE,
    )
