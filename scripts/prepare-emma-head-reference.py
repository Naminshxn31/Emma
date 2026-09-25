"""Prepare measured, uniformly scaled reference canvases; no model rendering.

Run with project Python (Pillow), before the Blender head-only build.
Every contour remains in source pixels and is independently reviewable.
"""
from pathlib import Path
import hashlib
import json
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from emma_head_reference_data import SOURCE, VIEWS

OUT = ROOT / 'models/emma/head-match'
REF = OUT / 'references'
REF.mkdir(parents=True, exist_ok=True)
SIZE = 768
ORTHO_SCALE = 1.25
path = ROOT / SOURCE['path']
assert hashlib.sha256(path.read_bytes()).hexdigest() == SOURCE['sha256']
source = Image.open(path).convert('RGB')
assert source.size == tuple(SOURCE['size'])

required = ['head_width', 'head_height', 'head_depth', 'torso_width',
            'torso_height', 'torso_depth', 'head_to_torso_gap', 'shoulder_span',
            'arm_length_left', 'arm_length_right', 'hand_width', 'hand_height',
            'hover_ring_width', 'hover_ring_depth', 'body_to_hover_ring_gap']
measurements = dict.fromkeys(required)
front = VIEWS['front']
measurements.update(head_width=1.0,
                    head_height=(front['bounds'][3]-front['bounds'][1])/front['width'])
measurements.update({
    'status': 'draft / head-only / visual approval required',
    'phase': 'A-D', 'units': 'front head width = 1.0',
    'source': SOURCE, 'views': VIEWS,
    'measurement_method': 'Manual source-pixel contours; front/back uniformly scaled by head width, quarter uniformly scaled by vertical extent to preserve foreshortening. No anisotropic scaling, pose fitting, or perspective warping.',
    'comparison': {'size': SIZE, 'ortho_scale': ORTHO_SCALE,
                   'pixels_per_unit': SIZE/ORTHO_SCALE,
                   'overlay_opacity': .5,
                   'source_pixel_tolerance': 3.0,
                   'tolerance_status': 'Review aid reflecting the small reference; not owner acceptance.'},
    'unmeasured': 'Null values belong to deferred phases or lack a calibrated orthographic reference. They are not zero.',
    'construction_assumptions': {
        'symmetry': 'Average left/right radial contours about each measured head center. Report residual against raw source contours.',
        'shared_outline': 'Equal-weight front/back radial contours: one orthographic solid cannot have two different front/back silhouettes. Both raw source residuals are reported.',
        'eye_placement': 'Raw measured projected eye shapes/centers take priority over forced symmetry. The slight turn in the source shifts the pair from the dome center; canonical orthographic facial symmetry remains unverified.',
        'head_depth': .68,
        'head_depth_status': 'Provisional construction value, not a measurement; no true 90-degree source exists in the local sheet.',
        'quarter_camera_yaw_degrees': 55,
        'quarter_camera_status': 'Illustrative, uncalibrated; excluded from quantitative acceptance.',
        'surface_relief': 'Panel/eye relief is provisional; the available drawings constrain projection more strongly than hidden depth.'
    }
})

for view, data in VIEWS.items():
    width = data['width']
    if view == 'quarter':
        # A turned head is narrower; normalizing that narrowed width to 1 would
        # hide depth information. Use vertical extent with a uniform XY scale.
        width = (data['bounds'][3]-data['bounds'][1])/measurements['head_height']
    data['normalization_pixels_per_unit'] = width
    cx, cy = data['center']
    scale = SIZE / ORTHO_SCALE / width
    # Destination -> source affine; equal X/Y scale is essential.
    coefficients = (1/scale, 0, cx-SIZE/2/scale,
                    0, 1/scale, cy-SIZE/2/scale)
    canvas = source.transform((SIZE, SIZE), Image.Transform.AFFINE,
                              coefficients, Image.Resampling.BICUBIC)
    canvas.save(REF / f'{view}.png')
    source.crop(data['crop']).save(REF / f'{view}_native.png')
    trace = canvas.copy()
    draw = ImageDraw.Draw(trace)
    for name, points in data['contours'].items():
        transformed = [((x-cx)*scale+SIZE/2, (y-cy)*scale+SIZE/2) for x,y in points]
        color = (100,255,180) if name == 'head' else (255,170,80)
        draw.line(transformed + transformed[:1], fill=color, width=2)
        mask = Image.new('L', (SIZE,SIZE), 0)
        ImageDraw.Draw(mask).polygon(transformed, fill=255)
        mask.save(REF / f'{view}_{name}_mask.png')
    trace.save(REF / f'{view}_trace.png')

(OUT / 'measurements.json').write_text(json.dumps(measurements, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
print(json.dumps({'source_verified': True, 'views': list(VIEWS),
                  'head_width': measurements['head_width'],
                  'head_height': measurements['head_height'], 'head_depth': None}))
