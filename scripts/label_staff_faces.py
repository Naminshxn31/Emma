#!/usr/bin/env python3
"""
Put names to the staff portraits, once, on a page instead of in a spreadsheet.

    python scripts/label_staff_faces.py                 # build the page
    python scripts/label_staff_faces.py --open          # and open it

The 43 photographs in `D:\\Data\\รูปพนักงาน` are studio portraits with
filenames like `DSC09657.jpg`. They are the best enrolment set this project
will ever get — one person per frame, front on, evenly lit — and not one of
them says who it is. Matching camera numbers to colleagues in a spreadsheet
means holding a face in your head while you scroll, which is where the
`script_approved` bug came from: a boring one-time job done in a form that
makes a mistake invisible.

So this writes a page with the face beside the box you type into. It has no
server: the images are embedded, and the Save button hands back a CSV.
Nothing here talks to the gallery — `build_face_gallery.py` reads the CSV
afterwards, so a name typed wrong can be fixed by editing one line rather
than by re-running anything.

Whose face is on a screen matters. The page is a local file, the crops are
downscaled to 320px, and neither it nor the CSV belongs anywhere near the
repo — `data/faces/` is gitignored for exactly this.
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import json
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STAFF_DIR = Path("D:/Data/รูปพนักงาน")
OUT_HTML = ROOT / "data" / "faces" / "label_staff.html"
OUT_CSV = ROOT / "data" / "faces" / "staff.csv"

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>ใส่ชื่อพนักงาน</title>
<style>
  body {{ font-family: system-ui, "Segoe UI", sans-serif; margin: 24px;
         background: #f6f6f7; color: #16161a; }}
  h1 {{ font-size: 20px; }}
  p.note {{ color: #55555c; max-width: 60ch; line-height: 1.6; }}
  .grid {{ display: grid; gap: 18px;
           grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); }}
  .card {{ background: #fff; border-radius: 12px; padding: 10px;
           box-shadow: 0 1px 3px rgba(0,0,0,.12); }}
  .card img {{ width: 100%; border-radius: 8px; display: block; }}
  .card code {{ font-size: 11px; color: #7a7a83; }}
  .card input {{ width: 100%; margin-top: 6px; padding: 7px 8px; font-size: 15px;
                 border: 1px solid #cfcfd6; border-radius: 7px; box-sizing: border-box; }}
  .card input:focus {{ outline: 2px solid #4a6cf7; border-color: transparent; }}
  .bar {{ position: sticky; top: 0; background: #f6f6f7; padding: 12px 0;
          z-index: 5; display: flex; gap: 12px; align-items: center; }}
  button {{ font-size: 15px; padding: 9px 18px; border-radius: 8px; border: 0;
            background: #16161a; color: #fff; cursor: pointer; }}
  #count {{ color: #55555c; }}
</style>
<h1>ใส่ชื่อพนักงาน ({n} รูป)</h1>
<p class="note">
  พิมพ์ชื่อที่อยากให้หุ่นเรียก (เช่น "คุณมิ้น") ช่องล่างคือตำแหน่ง ใส่หรือไม่ใส่ก็ได้
  รูปไหนไม่รู้ว่าใครให้เว้นว่าง — เว้นว่างแปลว่าไม่ลงทะเบียน ดีกว่าเดา
  <br>กด <b>บันทึก CSV</b> แล้วเอาไฟล์ไปวางที่ <code>data/faces/staff.csv</code>
</p>
<div class="bar">
  <button onclick="save()">บันทึก CSV</button>
  <span id="count"></span>
</div>
<div class="grid">
{cards}
</div>
<script>
const files = {files};
function tally() {{
  let n = 0;
  for (let i = 0; i < files.length; i++)
    if (document.getElementById('n_' + i).value.trim()) n++;
  document.getElementById('count').textContent = n + ' / ' + files.length + ' ใส่ชื่อแล้ว';
}}
document.addEventListener('input', tally);
tally();
function save() {{
  const rows = [['file', 'name', 'role']];
  for (let i = 0; i < files.length; i++) {{
    const name = document.getElementById('n_' + i).value.trim();
    const role = document.getElementById('r_' + i).value.trim();
    if (name) rows.push([files[i], name, role]);
  }}
  const csv = rows.map(r => r.map(c => '"' + c.replace(/"/g, '""') + '"').join(',')).join('\\n');
  const blob = new Blob(['\\ufeff' + csv], {{ type: 'text/csv;charset=utf-8' }});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'staff.csv';
  a.click();
}}
</script>
"""

# Indexed ids, not filenames. One of the 43 photographs is called
# "ดีไซน์ที่ยังไม่ได้ตั้งชื่อ (10).png" — spaces and brackets, which an HTML
# id may not contain, so keying the inputs by filename left that card wired
# to nothing and its name silently absent from the CSV.
CARD = """  <div class="card">
    <img src="data:image/jpeg;base64,{b64}" alt="">
    <code>{file}</code>
    <input id="n_{i}" placeholder="ชื่อที่หุ่นจะเรียก" value="{name}">
    <input id="r_{i}" placeholder="ตำแหน่ง (ไม่บังคับ)" value="{role}">
  </div>"""


def existing_labels(path: Path) -> dict[str, tuple[str, str]]:
    """Names already typed, so a second run does not start from blank.

    Re-running to add three new photographs must not cost forty names.
    """
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {r["file"]: (r.get("name", ""), r.get("role", ""))
                for r in csv.DictReader(fh) if r.get("file")}


def crop(path: Path, size: int = 320) -> str:
    """A face-sized JPEG, base64, for embedding.

    Uses the detector when the models are on this machine so the crop is
    actually centred on the face; falls back to the top square of the frame,
    which is where a portrait's head is anyway. The fallback matters: naming
    the photographs is a job that should not need a 280MB download first.
    """
    from PIL import Image

    from app import faces

    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        box = None
        if faces.available():
            face = faces.embed_file(path)
            if face is not None:
                x1, y1, x2, y2 = face.bbox
                pad = int(0.45 * max(x2 - x1, y2 - y1))
                box = (max(0, x1 - pad), max(0, y1 - pad),
                       min(w, x2 + pad), min(h, y2 + pad))
        if box is None:
            side = min(w, h)
            box = ((w - side) // 2, 0, (w - side) // 2 + side, side)
        im = im.crop(box)
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=STAFF_DIR)
    ap.add_argument("--open", action="store_true", help="open the page when done")
    args = ap.parse_args()

    if not args.dir.is_dir():
        print(f"no such folder: {args.dir}")
        return 1
    exts = {".jpg", ".jpeg", ".png"}
    photos = sorted(p for p in args.dir.iterdir() if p.suffix.lower() in exts)
    if not photos:
        print(f"no photos in {args.dir}")
        return 1

    known = existing_labels(OUT_CSV)
    print(f"{len(photos)} photos, {len(known)} already named")

    cards = []
    for i, p in enumerate(photos, 1):
        name, role = known.get(p.name, ("", ""))
        cards.append(CARD.format(i=i - 1, b64=crop(p), file=html.escape(p.name),
                                 name=html.escape(name), role=html.escape(role)))
        print(f"  {i}/{len(photos)} {p.name}", flush=True)

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(
        PAGE.format(n=len(photos), cards="\n".join(cards),
                    files=json.dumps([p.name for p in photos], ensure_ascii=False)),
        encoding="utf-8")
    print(f"\nwrote {OUT_HTML}")
    print(f"fill it in, save the CSV to {OUT_CSV}, then:")
    print("    python scripts/build_face_gallery.py")
    if args.open:
        webbrowser.open(OUT_HTML.resolve().as_uri())
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass
    sys.exit(main())
