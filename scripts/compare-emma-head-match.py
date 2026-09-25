"""Compare real orthographic renders with the unwarped source contours.

Run after the Blender build. Measurements are against human source traces, not
ground-truth CAD. Side/depth acceptance is deliberately left unresolved.
"""
from pathlib import Path
import json

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'models/emma/head-match'
VALIDATION=OUT/'validation'
data=json.loads((OUT/'measurements.json').read_text(encoding='utf-8'))
audit=json.loads((OUT/'model-audit.json').read_text(encoding='utf-8'))
N=data['comparison']['size']
font_path=Path('C:/Windows/Fonts/arial.ttf')
font=ImageFont.truetype(str(font_path),22)
small=ImageFont.truetype(str(font_path),17)
bg=(26,29,30)
metrics={}
rows=[]

for number,view in [(1,'front'),(2,'side'),(3,'back'),(4,'quarter')]:
    path=VALIDATION/f'{number:02}_{view}.png'
    rendered=Image.open(path).convert('RGBA')
    assert rendered.size==(N,N)
    alpha=np.asarray(rendered.getchannel('A'))
    flat=Image.new('RGB',(N,N),bg)
    flat.paste(rendered,mask=rendered.getchannel('A'))
    flat.save(VALIDATION/f'{view}_render.png')
    if view=='side':
        metrics[view]={'status':'UNVERIFIED','reason':'No calibrated true-side source; the middle drawing is a three-quarter view. No side overlay fabricated.'}
        continue
    ref=Image.open(OUT/'references'/f'{view}.png').convert('RGB')
    target_mask=np.asarray(Image.open(OUT/'references'/f'{view}_head_mask.png'))>127
    # Remove the rest of the source sheet outside a narrow margin; the head
    # pixels themselves are only uniformly resampled, never generated or warped.
    margin=cv2.dilate(target_mask.astype(np.uint8),np.ones((9,9),np.uint8))*255
    reference=Image.new('RGB',(N,N),bg)
    reference.paste(ref,mask=Image.fromarray(margin))
    reference.save(VALIDATION/f'{view}_reference.png')
    overlay=Image.blend(reference,flat,.5)
    overlay.save(VALIDATION/f'{view}_overlay.png')
    model_mask=alpha>127
    intersection=np.logical_and(target_mask,model_mask).sum()
    union=np.logical_or(target_mask,model_mask).sum()
    target_edge=target_mask.astype(np.uint8)-cv2.erode(target_mask.astype(np.uint8),np.ones((3,3),np.uint8))
    model_edge=model_mask.astype(np.uint8)-cv2.erode(model_mask.astype(np.uint8),np.ones((3,3),np.uint8))
    to_target=cv2.distanceTransform(1-target_edge,cv2.DIST_L2,5)
    to_model=cv2.distanceTransform(1-model_edge,cv2.DIST_L2,5)
    distances=np.concatenate([to_target[model_edge>0],to_model[target_edge>0]])
    source_scale=N/data['comparison']['ortho_scale']/data['views'][view]['normalization_pixels_per_unit']
    diff=np.zeros((N,N,3),dtype=np.uint8);diff[:]=bg
    diff[target_mask&model_mask]=(175,180,180)
    diff[target_mask&~model_mask]=(230,72,158)
    diff[model_mask&~target_mask]=(60,211,241)
    Image.fromarray(diff).save(VALIDATION/f'{view}_difference.png')
    my,mx=np.where(model_mask);ty,tx=np.where(target_mask)
    entry={'iou':float(intersection/union),
           'boundary_mean_source_px':float(np.mean(distances)/source_scale),
           'boundary_p95_source_px':float(np.quantile(distances,.95)/source_scale),
           'boundary_max_source_px':float(np.max(distances)/source_scale),
           'render_width_height_px':[int(mx.max()-mx.min()+1),int(my.max()-my.min()+1)],
           'reference_width_height_px':[int(tx.max()-tx.min()+1),int(ty.max()-ty.min()+1)],
           'status':'review required',
           'reference_uncertainty':data['views'][view]['uncertainty']}
    if view=='quarter':
        entry['status']='ILLUSTRATIVE ONLY: uncalibrated yaw and depth; NOT an acceptance result'
    else:
        entry['within_trace_review_tolerance']=entry['boundary_p95_source_px']<=data['comparison']['source_pixel_tolerance']
    metrics[view]=entry
    if view in ('front','back'):
        row=Image.new('RGB',(1200,480),bg)
        draw=ImageDraw.Draw(row)
        draw.text((12,8),f'{view.upper()}  |  original / clay render / 50% overlay',font=font,fill='white')
        for i,img in enumerate((reference,flat,overlay)):
            row.paste(img.resize((400,400),Image.Resampling.LANCZOS),(400*i,45))
        draw.text((12,450),f"Trace IoU {entry['iou']:.4f}  |  95% boundary error {entry['boundary_p95_source_px']:.2f} source px  |  not visual approval",font=small,fill=(171,211,209))
        rows.append(row)

board=Image.new('RGB',(1200,1120),bg)
board.paste(rows[0],(0,0));board.paste(rows[1],(0,480))
draw=ImageDraw.Draw(board)
draw.text((18,985),'HEAD ONLY / PHASE A-D / DRAFT',font=font,fill='white')
draw.text((18,1020),'Front width = 1.0. Reference images use uniform scale; no stretching or perspective fitting.',font=small,fill='white')
draw.text((18,1050),'SIDE depth is provisional. True orthographic side render supplied; matching source is missing.',font=small,fill=(240,194,124))
draw.text((18,1080),'No rig, animation, torso, hand work or GLB export in this scene.',font=small,fill=(175,190,195))
board.save(VALIDATION/'head-comparison.png')

part_metrics={}
for part in ('face','chin','chrome_wave','eye_l','eye_r'):
    actual=np.asarray(Image.open(VALIDATION/f'front_{part}_geometry.png').getchannel('A'))>127
    target=np.asarray(Image.open(OUT/'references'/f'front_{part}_mask.png'))>127
    edge_a=actual.astype(np.uint8)-cv2.erode(actual.astype(np.uint8),np.ones((3,3),np.uint8))
    edge_b=target.astype(np.uint8)-cv2.erode(target.astype(np.uint8),np.ones((3,3),np.uint8))
    distance=np.concatenate([cv2.distanceTransform(1-edge_a,cv2.DIST_L2,5)[edge_b>0],
                             cv2.distanceTransform(1-edge_b,cv2.DIST_L2,5)[edge_a>0]])
    scale=N/data['comparison']['ortho_scale']/data['views']['front']['width']
    part_metrics[part]={'iou':float((actual&target).sum()/(actual|target).sum()),
                        'boundary_p95_source_px':float(np.quantile(distance,.95)/scale),
                        'method':'Actual isolated mesh alpha vs raw source contour; not an occlusion/intersection test.'}

results={'phase':'A-D','status':'DRAFT - NOT APPROVED','geometry_approved':False,
         'source_verified':True,'comparison_method':data['measurement_method'],
         'metrics':metrics,'front_component_metrics':part_metrics,
         'armatures':audit['armatures'],'actions':audit['actions'],
         'blocking_review_items':['True-side depth is unmeasured.','Owner visual approval has not been given.',
                                  'Measured projected eye centers retain source asymmetry; canonical facial symmetry remains unverified.',
                                  'Raw reference has slight turn/asymmetry; a symmetric orthographic model cannot reproduce every photographed pixel.'],
         'completed':['Measured source head contours','Orthographic front/side/back clay renders',
                      'Front/back 50% overlays and contour differences','Modular white panels/chrome wave/happy eyes'],
         'deferred':['Torso','Arms and hands','Material polish','Rig','Expressions','Animation','GLB/FBX','Web UI']}
assert results['armatures']==0 and results['actions']==0
assert all(c['type']=='ORTHO' for c in audit['cameras'].values())
(VALIDATION/'acceptance.json').write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
print(json.dumps({k:{i:v for i,v in d.items() if i in ('iou','boundary_p95_source_px','status')} for k,d in metrics.items()},indent=2))
