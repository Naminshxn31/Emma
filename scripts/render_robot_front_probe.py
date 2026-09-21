"""Review actual front-camera frames; never infer angles from servo commands."""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    base = args.folder
    meta = json.loads((base / 'frames.json').read_text(encoding='utf-8'))
    frames = meta['frames']
    dispatch = meta['command_process_started_seconds']
    if dispatch is None or not frames:
        raise SystemExit('No command recording to review')
    serial = json.loads((base / 'serial.json').read_text(encoding='utf-8'))
    title = f"Channel {meta['channel']} / commanded {serial['motion_result']['at']}"
    crop = (330, 70, 630, 560)  # Inspected front-camera robot bounds.
    font = ImageFont.truetype('C:/Windows/Fonts/tahoma.ttf', 15)
    targets = [dispatch-.3, dispatch+1, dispatch+2, frames[-1]['seconds']-.2]
    selected = [min(frames, key=lambda f:abs(f['seconds']-t)) for t in targets]
    sheet = Image.new('RGB', (1200, 550), '#071321')
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 4), title + ' | Actual camera images; t relative to command process start', font=font, fill='white')
    for i, f in enumerate(selected):
        with Image.open(base / f['file']) as source:
            sheet.paste(source.crop(crop), (300*i, 55))
        draw.text((300*i+8, 29), f"t={f['seconds']-dispatch:+.2f}s", font=font, fill='#8eeaff')
    sheet.save(base / 'review.png')
    replay = []
    sampled = frames[::2]
    for f in sampled:
        im = Image.new('RGB', (300, 535), '#071321')
        with Image.open(base / f['file']) as source:
            im.paste(source.crop(crop), (0, 0))
        d = ImageDraw.Draw(im)
        d.text((8, 492), title, font=font, fill='white')
        d.text((8, 513), f"t={f['seconds']-dispatch:+.2f}s", font=font, fill='#8eeaff')
        replay.append(im)
    durations = [max(20, round((b['seconds']-a['seconds'])*1000)) for a,b in zip(sampled,sampled[1:])] + [200]
    replay[0].save(base / 'replay.gif', save_all=True, append_images=replay[1:], duration=durations, loop=0)
    print(json.dumps({'channel':meta['channel'], 'frames':len(frames), 'rx_bytes':serial['rx_bytes'], 'armed_after':serial['armed_after']}))


if __name__ == '__main__':
    main()
