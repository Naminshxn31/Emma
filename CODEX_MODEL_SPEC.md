# CODEX_MODEL_SPEC.md
# EMMA — Reference-Locked 3D Character Build Specification

## 0. PRIMARY DIRECTIVE

Rebuild EMMA as a reference-locked 3D character.

The supplied character design sheets are the SINGLE SOURCE OF TRUTH.

DO NOT redesign the character.
DO NOT reinterpret proportions.
DO NOT replace shapes with generic robot parts.
DO NOT use the current draft model as a shape reference.
DO NOT proceed to rigging until geometry matching passes review.

When this document conflicts with the current 3D model:
THE REFERENCE IMAGES WIN.

Priority:

1. Silhouette accuracy
2. Proportion accuracy
3. Major shape accuracy
4. Face accuracy
5. Component placement
6. Material appearance
7. Clean topology
8. Rigging
9. Animation

Never sacrifice items 1–5 to finish items 6–9.


# 1. CHARACTER IDENTITY

Character name:
EMMA

Visual language:
- cute floating companion robot
- compact proportions
- oversized head
- small teardrop torso
- short floating arms
- soft rounded forms
- glossy white shell
- chrome structural/details
- black facial details
- white emissive lighting
- no legs
- luminous hover ring below body

IMPORTANT:

EMMA is NOT humanoid.

Do not introduce:
- human shoulders
- human neck anatomy
- realistic elbows
- realistic wrists
- muscular forms
- mechanical complexity not visible in reference
- panel lines not visible in reference
- extra bolts
- extra vents
- sci-fi greebles


# 2. REFERENCE WORKFLOW

Before modifying geometry:

Import the supplied reference images into Blender.

Create collection:

REF_IMAGES

Create orthographic reference setups:

REF_FRONT
REF_SIDE
REF_BACK

Reference planes must not render in final output.

Create cameras:

CAM_COMPARE_FRONT
CAM_COMPARE_SIDE
CAM_COMPARE_BACK

All comparison cameras MUST use ORTHOGRAPHIC projection.

Never compare perspective renders against orthographic/reference views.


# 3. SCALE SYSTEM

Use normalized character height.

Define:

HEAD_DIAMETER = 1.0 unit

All other measurements should be expressed relative to HEAD_DIAMETER.

Do not guess physical centimeters yet.

Character origin:

X = horizontal
Y = depth
Z = vertical

World center:
X = 0

Character symmetry plane:
X = 0

Front direction:
-Y

Up:
+Z


# 4. PROPORTION LOCK

Do NOT derive proportions from generic character design rules.

Measure proportions directly from the supplied character sheet.

Create:

measurements.json

Store at minimum:

{
  "head_width": null,
  "head_height": null,
  "head_depth": null,

  "torso_width": null,
  "torso_height": null,
  "torso_depth": null,

  "head_to_torso_gap": null,

  "shoulder_span": null,

  "arm_length_left": null,
  "arm_length_right": null,

  "hand_width": null,
  "hand_height": null,

  "hover_ring_width": null,
  "hover_ring_depth": null,

  "body_to_hover_ring_gap": null
}

Populate these values by measuring the reference.

DO NOT invent measurements before analyzing the images.


# 5. HEAD

Object:

HEAD_SHELL

The head is the most important component.

The head MUST NOT be created as a simple UV sphere and left unchanged.

Start from suitable topology, then reshape it to match:

FRONT silhouette
SIDE silhouette
BACK silhouette

The head is:
- large
- rounded
- smooth
- slightly flattened compared with a perfect sphere
- visually dominant over the torso

Match the reference silhouette before adding facial components.


## 5.1 Head shell layers

Recommended objects:

HEAD_SHELL_WHITE
HEAD_OUTER_CHROME
FACE_LOWER_CHROME

Keep major visual pieces separate where practical.


## 5.2 Lower face chrome wave

This is a signature feature.

Object:

FACE_LOWER_CHROME

It must reproduce the exact broad wave / curved lower-face shape shown in the reference.

DO NOT turn it into:
- a generic smile
- a simple curved tube
- a moustache
- a mouth line

Its FRONT silhouette is critical.

Its transition into the side chrome structure must also match SIDE reference.


# 6. FACE SYSTEM

Facial components must be modular.

Collection:

FACE_PARTS


## Eyes

Create interchangeable eye components.

Naming example:

EYE_HAPPY_L
EYE_HAPPY_R

EYE_CLOSED_L
EYE_CLOSED_R

EYE_X_L
EYE_X_R

Additional expressions may be added only when supported by reference.


## Mouth

Examples:

MOUTH_SMILE_01
MOUTH_SMILE_02
MOUTH_OPEN_01
MOUTH_OPEN_02


## Rules

Facial components:
- must remain separate meshes
- must follow head curvature
- must not float visibly above the shell
- must not intersect badly with the shell
- must match positions shown in reference

Do not permanently merge expressions into HEAD_SHELL.


# 7. TORSO

Main object:

TORSO_SHELL

The torso is NOT a sphere.

Front silhouette:
rounded inverted teardrop / shield-like form.

The upper torso is wider.

The lower torso narrows toward a rounded bottom point.

Match FRONT, SIDE and BACK independently.


# 8. CHEST LIGHT

Objects:

CHEST_RING
CHEST_CORE

Chest light must be centered according to reference.

Use separate geometry where practical.

CHEST_RING:
chrome / dark structural surround

CHEST_CORE:
white emissive element

Do not exaggerate its depth.


# 9. NECK / FLOATING CONNECTION

The visual gap between head and torso is important.

Do not create a conventional humanoid neck.

Use the small circular connection/ring visible in reference.

Object:

NECK_RING

Maintain visible separation between:
HEAD
NECK_RING
TORSO


# 10. SHOULDERS AND ARMS

Collections:

ARM_L
ARM_R

Do NOT model human shoulder anatomy.

The shoulder connection should reproduce the compact mechanical ring/socket shown in reference.

Objects may include:

SHOULDER_SOCKET_L
UPPER_ARM_L
WRIST_RING_L
HAND_SOCKET_L

SHOULDER_SOCKET_R
UPPER_ARM_R
WRIST_RING_R
HAND_SOCKET_R

Keep visual complexity consistent with reference.

Do not add exposed machinery unless explicitly visible.


# 11. HAND SYSTEM

Hands are signature character components.

Collection:

HANDS


Required variants:

HAND_PALM_L
HAND_PALM_R

HAND_FIST_L
HAND_FIST_R


Optional later:

HAND_POINT_L
HAND_POINT_R


Hands should feel:
- soft
- rounded
- oversized relative to arm
- cartoon-like
- glove-inspired

Avoid:
- realistic human knuckles
- fingernails
- skin anatomy
- thin fingers

Hands must match supplied hand reference images.


# 12. HOVER RING

Object:

HOVER_RING

Separate from body.

Shape:
elliptical luminous ring when seen from camera perspective.

Actual geometry should be a clean circular/elliptical ring as required by the design.

Material:

MAT_EMISSION_WHITE

The ring must NOT physically connect to torso.

Position it directly underneath EMMA according to reference.


# 13. MATERIALS

Create exactly these base materials:

MAT_WHITE_GLOSS
MAT_CHROME
MAT_BLACK
MAT_EMISSION_WHITE


## MAT_WHITE_GLOSS

Use for:
- main head shell
- torso shell
- hands
- white arm components

Properties:
- glossy white
- smooth
- slightly soft highlight
- not transparent
- not metallic


## MAT_CHROME

Use for:
- structural rings
- head trim
- lower face chrome feature
- selected torso trim
- wrist/shoulder details

Properties:
- metallic
- highly reflective
- clean chrome

Do not make every surface chrome.


## MAT_BLACK

Use for:
- facial details
- dark structural details where reference requires them


## MAT_EMISSION_WHITE

Use for:
- chest illumination
- hover ring
- explicitly glowing details

Glow must not destroy silhouette readability.


# 14. TOPOLOGY

During MODEL MATCH phase:

Accuracy > topology optimization.

However:
- use clean quads where reasonable
- maintain symmetry
- avoid destructive triangulation
- avoid unnecessary dense geometry
- keep Subdivision Surface compatibility

Use Mirror modifier where appropriate.

Do not apply destructive modifiers until geometry is approved.


# 15. REQUIRED OBJECT ORGANIZATION

Scene collections:

EMMA
├── GEO
│   ├── HEAD
│   ├── FACE
│   ├── TORSO
│   ├── ARM_L
│   ├── ARM_R
│   ├── HANDS
│   └── HOVER
│
├── MATERIALS
├── RIG
├── REF_IMAGES
├── CAMERAS
└── LIGHTING


# 16. MODEL MATCHING LOOP

This step is mandatory.

For each view:

FRONT
SIDE
BACK

Perform:

1. Render current model with orthographic camera.
2. Load corresponding reference.
3. Match image dimensions.
4. Align character centers.
5. Produce 50% opacity overlay.
6. Produce difference image if possible.
7. Inspect silhouette mismatch.
8. Correct geometry.
9. Repeat.

Output:

validation/
├── front_render.png
├── front_overlay.png
├── side_render.png
├── side_overlay.png
├── back_render.png
└── back_overlay.png


# 17. SILHOUETTE ACCEPTANCE TEST

Before rigging, verify:

[ ] Head width matches reference.
[ ] Head height matches reference.
[ ] Head side depth matches reference.
[ ] Chrome head trim follows reference.
[ ] Lower face wave matches reference.
[ ] Eye placement matches reference.
[ ] Torso width matches reference.
[ ] Torso height matches reference.
[ ] Torso bottom point matches reference.
[ ] Head/torso gap matches reference.
[ ] Shoulder positions match reference.
[ ] Arm length matches reference.
[ ] Hand scale matches reference.
[ ] Hover ring size matches reference.
[ ] Hover ring vertical position matches reference.

If any major item fails:

DO NOT START RIGGING.


# 18. RIGGING PHASE

Only begin after MODEL MATCH is approved.

Armature:

RIG_EMMA


Suggested hierarchy:

ROOT
└── BODY_MASTER
    ├── TORSO
    ├── HEAD
    │   └── FACE_ROOT
    ├── SHOULDER_L
    │   └── ARM_L
    │       └── HAND_L
    └── SHOULDER_R
        └── ARM_R
            └── HAND_R

Separate control:

HOVER_RING_CTRL


# 19. RIG BEHAVIOR

Required controls:

CTRL_ROOT
CTRL_BODY
CTRL_HEAD
CTRL_ARM_L
CTRL_ARM_R
CTRL_HAND_L
CTRL_HAND_R
CTRL_HOVER_RING

Optional:

CTRL_LOOK


Because arms are short and stylized:

Do not over-engineer humanoid IK.

Use simple robust FK first.

Add IK only if it improves animation workflow without deforming the design.


# 20. FACE EXPRESSION SYSTEM

Prefer object visibility / swap system for discrete face components.

Do not force all facial expressions into skeletal deformation.

Possible implementation:

custom property:

expression

or:

visibility drivers

Expressions should reference the supplied expression sheet.


# 21. HAND SWAP SYSTEM

Create custom property:

hand_pose_L
hand_pose_R

Possible values:

PALM
FIST
POINT

Only show one hand variant per side at a time.


# 22. TEST POSES

After rigging, reproduce reference poses.

Minimum:

POSE_REST
POSE_HELLO
POSE_OPEN_HANDS
POSE_LOOK_AROUND

Additional expression tests:

HAPPY
SAD
EXCITED
CALM

Do not alter base proportions to make poses easier.


# 23. BLENDER SCRIPTING

Create reproducible Blender Python scripts.

Recommended:

scripts/
├── 01_setup_scene.py
├── 02_build_materials.py
├── 03_build_model.py
├── 04_validation_render.py
├── 05_build_rig.py
├── 06_setup_face_swaps.py
├── 07_setup_hand_swaps.py
└── 08_export.py

Scripts must be safe to rerun when practical.

Do not rely solely on manual Blender operations.


# 24. OUTPUT FILES

Required final outputs:

EMMA_MASTER.blend
EMMA_RIGGED.glb
EMMA_RIGGED.fbx

Also retain:

measurements.json

and validation renders.


# 25. GLB EXPORT

Before GLB export verify:

[ ] transforms are correct
[ ] scale is consistent
[ ] materials work
[ ] emission works
[ ] armature works
[ ] animation clips are named
[ ] hidden reference objects are excluded
[ ] cameras excluded unless required
[ ] unused objects excluded
[ ] hand variants behave correctly


# 26. CRITICAL FAILURE CONDITIONS

STOP and fix the model if:

- head looks like a generic sphere
- torso looks spherical
- character becomes humanoid
- face proportions drift
- lower chrome wave differs significantly
- head/body ratio differs significantly
- hands become realistic
- arms become too long
- shoulders become anatomically human
- extra mechanical details appear
- model matches previous draft better than reference

The previous draft is NOT the target.

REFERENCE SHEET = TARGET.


# 27. CODEX EXECUTION ORDER

Codex must work in this exact order:

PHASE A
Analyze reference.

PHASE B
Record measurements.

PHASE C
Build head.

PHASE D
Validate head FRONT/SIDE/BACK.

PHASE E
Build torso.

PHASE F
Validate torso FRONT/SIDE/BACK.

PHASE G
Build arms and hands.

PHASE H
Build face components.

PHASE I
Build hover ring.

PHASE J
Apply materials.

PHASE K
Generate final orthographic overlays.

STOP HERE.

Request visual approval.

DO NOT CONTINUE AUTOMATICALLY TO RIGGING.


# 28. FINAL INSTRUCTION TO CODEX

You are not designing EMMA.

You are reconstructing EMMA.

When uncertain, inspect and measure the reference instead of inventing geometry.

Do not compensate for inaccurate geometry with camera angle, perspective,
lighting, pose, or materials.

A plain orthographic clay render with accurate geometry is more valuable
than a beautiful render with inaccurate geometry.

The model must first look correct in:

FRONT
SIDE
BACK

Only then may rigging begin.
