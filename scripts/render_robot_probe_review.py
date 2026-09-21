"""Render a comparison and replay from real observer frames, without synthesis."""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data/robot-inspection/console-ui-20260911'
font = ImageFont.truetype('C:/Windows/Fonts/tahoma.ttf', 19)
small = ImageFont.truetype('C:/Windows/Fonts/tahoma.ttf', 15)
crop = (300, 65, 640, 560)  # Observed robot bounds; exclude the computer monitors.
sheet = Image.new('RGB', (1052, 1200), '#071321')
draw = ImageDraw.Draw(sheet)
draw.text((20, 12), 'ภาพกล้องจริง: ช่อง 1 — เปรียบเทียบก่อนและหลังคำสั่ง', font=font, fill='#8eeaff')
draw.text((20, 43), 'เวลาเทียบกับการเริ่มโปรแกรมส่งคำสั่ง ไม่ใช่ค่าตำแหน่งหรือมุมข้อต่อ', font=small, fill='#ccd9e6')
for row, (folder, title) in enumerate((('visual-centre-3','สั่งค่า 1500 / T9999'), ('visual-step-2','สั่งค่า 1540 / T800'))):
    base = DATA / folder
    meta = json.loads((base / 'frames.json').read_text())
    dispatch = meta['command_process_started_seconds']
    moments = [dispatch-.3, dispatch+1, meta['frames'][-1]['seconds']-.2]
    selected = [min(meta['frames'], key=lambda f:abs(f['seconds']-t)) for t in moments]
    y = 78 + row*568
    draw.text((20,y), title, font=font, fill='white')
    for col, (frame,label) in enumerate(zip(selected, ['ก่อนเริ่ม','ระหว่างทดสอบ','หลังทดสอบ'])):
        x = 8 + col*348
        draw.text((x+5,y+29), f"{label}  t={frame['seconds']-dispatch:+.1f}s", font=small,fill='#ccd9e6')
        with Image.open(base / frame['file']) as im:
            sheet.paste(im.crop(crop), (x,y+51))
sheet.save(DATA / 'robot-camera-comparison.png')

base = DATA / 'visual-step-2'
meta = json.loads((base / 'frames.json').read_text())
selected = meta['frames'][::2]
replay = []
for f in selected:
    im = Image.new('RGB', (340,535), '#071321')
    with Image.open(base / f['file']) as source:
        im.paste(source.crop(crop),(0,0))
    ImageDraw.Draw(im).text((8,503), f"ช่อง 1 / 1540    t={f['seconds']-meta['command_process_started_seconds']:+.1f}s",font=small,fill='white')
    replay.append(im)
durations = [max(20,round((b['seconds']-a['seconds'])*1000)) for a,b in zip(selected,selected[1:])] + [200]
replay[0].save(DATA / 'robot-channel1-replay.gif',save_all=True,append_images=replay[1:],duration=durations,loop=0)
print('Rendered comparison and real-frame replay')
