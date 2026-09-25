"""Export source-pixel design contours, not replacement Emma artwork or a 3D mesh.

The original concept sheet stays byte-for-byte intact. Coordinates below refer
to that 1501 x 1008 image. Bezier curves are manual visual traces; eye contours
were sampled from the dark regions in the stated source ROI. Neither is an
orthographic engineering drawing. Only Python's standard library is required.
"""
from pathlib import Path
import hashlib
import json
import struct
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "client/assets/emma-reference-study"
SOURCE = ROOT / "client/assets/emma/emma-pose-library-reference-v1.png"
SOURCE_SHA256 = "f360f4d50a276f5715e6c73e45d4d487fe19364dd1ab77d70a30856b9a182878"
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

OUTER = "M 277 124 C 180 124 89 204 51 288 C 23 354 33 420 66 471 C 109 540 191 576 272 578 C 356 580 433 547 478 488 C 516 438 522 363 504 296 C 484 218 435 161 365 137 C 335 127 306 122 277 124 Z"
FACE = "M 284 147 C 192 146 118 217 93 292 C 77 337 75 392 97 423 C 118 408 140 397 163 400 C 204 403 233 445 259 476 C 271 491 279 498 292 490 C 316 475 337 442 366 423 C 399 402 430 405 452 421 C 461 427 467 433 474 440 C 491 412 493 371 489 335 C 481 254 440 189 378 161 C 347 147 315 145 284 147 Z"
CHIN = "M 142 438 C 156 427 176 426 192 438 C 214 454 234 500 256 516 C 269 529 280 534 296 525 C 328 505 351 473 376 453 C 395 436 411 432 424 440 C 441 451 442 468 437 486 C 429 513 406 533 376 548 C 328 574 276 579 232 566 C 187 555 150 535 132 507 C 117 483 118 457 142 438 Z"
EYE_L = "M 154 341 L 152 347 L 152 351 L 151 352 L 151 364 L 154 370 L 156 371 L 162 359 L 174 347 L 179 343 L 181 343 L 184 341 L 190 340 L 191 339 L 196 339 L 197 338 L 209 340 L 219 345 L 230 356 L 235 369 L 235 372 L 237 371 L 240 364 L 240 351 L 239 350 L 239 345 L 238 344 L 237 338 L 232 328 L 221 316 L 215 312 L 208 309 L 205 309 L 204 308 L 189 308 L 177 313 L 171 317 L 161 328 Z"
EYE_R = "M 379 336 L 377 342 L 373 345 L 367 363 L 367 375 L 368 378 L 370 380 L 371 380 L 373 374 L 378 367 L 386 360 L 392 356 L 404 351 L 413 351 L 414 352 L 417 352 L 423 354 L 433 362 L 439 371 L 442 380 L 442 384 L 441 386 L 444 384 L 447 376 L 447 362 L 446 361 L 447 359 L 446 358 L 446 354 L 442 345 L 442 343 L 440 341 L 439 338 L 430 328 L 416 321 L 406 320 L 405 321 L 400 321 L 391 325 Z"
BODY = "M 618 279 C 606 280 597 289 594 301 C 588 321 595 338 610 353 C 620 363 629 371 638 372 C 647 371 658 361 668 350 C 681 336 687 319 681 301 C 677 286 665 280 654 278 C 644 284 627 285 618 279 Z"
BODY_WHITE = "M 615 284 C 604 287 598 298 597 312 C 597 329 607 341 620 352 C 628 358 634 360 638 360 C 645 360 654 352 663 343 C 677 329 682 316 675 300 C 671 290 662 283 653 282 C 641 288 627 289 615 284 Z"
NECK_TOP = "M 620 274 C 628 278 646 278 656 274 C 657 279 651 283 638 284 C 626 284 620 280 620 274 Z"
NECK_BOTTOM = "M 618 280 C 627 285 649 285 659 279 C 657 283 650 286 638 286 C 627 286 620 283 618 280 Z"
SHOULDER_L = "M 562 284 C 569 277 576 277 581 282 C 587 287 591 297 588 303 C 586 305 583 306 581 304 C 580 296 574 287 565 287 Z"
SHOULDER_R = "M 695 283 C 701 277 709 279 713 286 C 705 287 699 297 698 304 C 697 307 692 305 690 303 C 686 296 689 288 695 283 Z"
HAND_L = "M 570 286 L 557 286 L 549 294 L 540 310 L 539 322 L 538 323 L 540 333 L 546 342 L 549 343 L 554 347 L 557 346 L 557 344 L 559 342 L 562 341 L 562 336 L 557 328 L 557 325 L 559 320 L 561 318 L 564 326 L 568 329 L 571 329 L 574 326 L 572 310 L 576 304 L 576 297 L 573 290 Z"
HAND_R = "M 703 286 L 699 292 L 698 300 L 697 301 L 698 306 L 702 310 L 699 324 L 700 326 L 704 329 L 709 327 L 713 315 L 716 312 L 717 313 L 715 316 L 716 319 L 714 320 L 717 325 L 717 329 L 712 335 L 711 338 L 712 340 L 714 341 L 715 344 L 718 346 L 722 346 L 728 340 L 729 340 L 734 331 L 734 328 L 735 327 L 734 313 L 729 301 L 725 295 L 716 286 Z"
OPEN_HAND = "M 56 519 C 70 511 85 521 97 533 C 105 542 108 550 111 555 C 123 562 131 570 141 580 C 159 587 168 602 168 618 C 171 638 155 655 139 666 C 132 676 119 683 109 679 C 98 678 95 667 98 661 C 90 666 81 670 70 664 C 60 660 54 651 58 642 L 57 639 C 50 645 42 639 35 634 C 28 626 32 614 40 600 C 48 583 59 572 63 566 C 51 551 44 538 49 529 C 50 523 53 519 56 519 Z"
RING = "M 591 393 C 591 387 611 383 637 383 C 663 383 682 387 682 393 C 682 399 663 402 637 402 C 611 402 591 399 591 393 Z M 600 393 C 600 390 616 388 637 388 C 658 388 674 390 674 393 C 674 396 658 398 637 398 C 616 398 600 396 600 393 Z"


def path(ident, label, d, color, fill="none"):
    return {"id": ident, "label": label, "d": d, "color": color, "fill": fill, "fillRule": "evenodd",
            "guide": "axis" in ident or ident=="palm-mass"}


def dim(label, value, note="ค่าประมาณจากภาพ ไม่ใช่ขนาดจริง"):
    return {"label": label, "value": value, "note": note}


def mark(label, x, y):
    return {"label": label, "x": x, "y": y}


PARTS = [
    {"id":"head", "step":"01", "name":"หัว · โครเมียม · คาง", "viewBox":[22,112,502,482],
     "observation":"กรอบหัวและรอยยิ้มเป็นผิวโครเมียมกว้างที่ต่อถึงกัน แบ่งหน้าขาวกับคางขาวเป็นสองพื้นที่ที่มองเห็น ความกว้างแถบเปลี่ยนตลอดแนวโค้ง",
     "construction":"เริ่มด้วยขอบหัว → แผ่นหน้าขาว → คาง แล้วเหลือพื้นที่ระหว่างชิ้นเป็นโครเมียม ตรวจเส้นซ้อนก่อนขึ้นผิวโค้งสามมิติ",
     "uncertainty":"รูปใหญ่มีมุมเฉียงและการเอียงหัว เส้นนี้เป็นรูปฉายในภาพ ความลึกต้องดูร่วมกับภาพ 3/4 และหลัง ส่วนขาวสองพื้นที่อาจเป็นเปลือกเดียวใต้กรอบ ยังยืนยันรอยต่อภายในไม่ได้",
     "paths":[path("chrome-field","พื้นที่โครเมียม",OUTER+" "+FACE+" "+CHIN,"#93ded0","#293f46"),
              path("face","หน้าขาว",FACE,"#9ebcf2","#e8ecef"),path("chin","คางขาว",CHIN,"#f2c788","#d6dfe2")],
     "landmarks":[mark("ยอดหัว",277,124),mark("แก้มซ้าย",163,400),mark("กลาง W",280,493),mark("แก้มขวา",411,409),mark("ปลาย W ล่าง",273,533)],
     "dimensions":[dim("ความสูง / กว้างหัว","≈ 0.95", "ภาพใหญ่; ขอบคร่าว ๆ x35–513, y124–578 ±3 px"),
                   dim("แถบยิ้มกลางหัว","≈ 6–8% W","ระยะระหว่างขอบสองฝั่งของแถบ ไม่ใช่รัศมีท่อ")],
     "method":"Manual cubic Bezier contours in hero source pixels; chrome is the shell area minus two enamel regions."},
    {"id":"eyes", "step":"02", "name":"ตาเสี้ยว", "viewBox":[132,284,332,121],
     "observation":"ตาเป็นเสี้ยวดำพองมน ขอบบนโค้งเต็ม ขอบล่างเว้า ปลายเรียวแต่ไม่เป็นยอดสามเหลี่ยม เส้นสะท้อนสีขาวอยู่บนผิว ไม่ใช่เส้นตาอีกชั้น",
     "construction":"ขึ้นตาข้างเดียวจากขอบนอกและขอบเว้าจริง แล้วสะท้อนอีกข้างในโมเดล ปักตำแหน่งจากภาพหน้าตรงก่อนจูนความนูน",
     "uncertainty":"ขนาดซ้าย–ขวาในรูปใหญ่ไม่เท่ากันจากมุมมอง ไม่ควรปั้นตาสองข้างให้ต่างขนาดตามภาพนี้ ความนูนด้านข้างยังต้องประมาณ",
     "paths":[path("eye-left","ตาซ้ายในภาพ",EYE_L,"#93ded0","#18252b"),path("eye-right","ตาขวาในภาพ",EYE_R,"#f2c788","#18252b")],
     "landmarks":[mark("L",195.5,340),mark("R",407,353)],
     "dimensions":[dim("ความกว้างตา / W หัว","≈ 0.18–0.19","จากภาพหน้าตรง ตา 32–33 px / หัวประมาณ 172 px"),
                   dim("ระดับศูนย์ตาจากยอดหัว","≈ 51–53% H","ภาพหน้าตรง: center y194.5; หัวประมาณ y110–275"),
                   dim("ระยะศูนย์ตาสองข้าง","≈ 0.46 W","ภาพหน้าตรงประมาณ 80 px")],
     "method":"Pixel contours: source ROI [120,275,470,400], grayscale <150, external connected contours, polygon tolerance 0.65 px. Highlights inside each eye remain holes in the photograph, not extra geometry."},
    {"id":"neck", "step":"03", "name":"คอสั้น", "viewBox":[604,263,69,35],
     "observation":"แกนคอดำสั้นซ่อนระหว่างหัวกับลำตัว มีแหวนเงาที่ขอบบน–ล่าง ช่องว่างเล็กมาก",
     "construction":"วางจุดหมุนใต้หัว สร้างแกนภายในก่อนครอบด้วยปากวงแหวน ให้ช่องว่างคอเล็กเท่าภาพหน้าตรง",
     "uncertainty":"ภาพไม่เห็นจุดหมุนภายในและคอด้านหลังทั้งหมด รูปวงแหวนในภาพเป็นวงรีจากมุมกล้อง",
     "paths":[path("neck-top","ขอบใต้หัว",NECK_TOP,"#93ded0","#273b42"),path("neck-lower","ขอบบนลำตัว",NECK_BOTTOM,"#f2c788","#18252b")],
     "landmarks":[mark("แนวศูนย์คอ",638,281)],
     "dimensions":[dim("ช่วงเชื่อมหัว–ลำตัว","≈ 0.03–0.06 W","ขอบบางส่วนทับกันและถูกบัง จึงใช้ช่วงค่า")],"method":"Manual trace from front assembled view."},
    {"id":"body", "step":"04", "name":"ลำตัว · แกนกลาง", "viewBox":[580,273,114,106],
     "observation":"ลำตัวเป็นหยดน้ำกลับหัว ด้านบนกว้าง ไหล่และด้านข้างมน ปลายล่างโค้งรับกัน หน้าขาวอยู่ในเปลือกโครเมียม แกนกลางมีขอบมืดและวงสว่าง",
     "construction":"ล็อกความกว้างกับหัวก่อน สร้างเปลือกเต็มใบ → แผ่นขาวหน้า/หลัง → เบ้าไฟวงกลม รักษาปลายล่างมนและไหล่กลม",
     "uncertainty":"ความลึกหน้าท้องและด้านใต้ไม่มีมุมตรง เบ้าไฟมีแสงฟุ้งจึงยังวัดความลึกจากภาพไม่ได้",
     "paths":[path("body-shell","เปลือกนอก",BODY,"#93ded0","#293f46"),path("body-enamel","หน้าขาว",BODY_WHITE,"#9ebcf2","#e8ecef"),
              path("core-bezel","เบ้าไฟ","M 617 317 A 21 21 0 1 0 659 317 A 21 21 0 1 0 617 317 Z","#f2c788","#293f46"),
              path("core-light","วงแสง","M 624 317 A 14 15 0 1 0 652 317 A 14 15 0 1 0 624 317 Z M 630 317 A 8 9 0 1 0 646 317 A 8 9 0 1 0 630 317 Z","#d2d8e1","#ffffff")],
     "landmarks":[mark("กลางเบ้าไฟ",638,317),mark("ปลายมน",638,372)],
     "dimensions":[dim("ความกว้างลำตัว / W หัว","≈ 0.55"),dim("ความสูงลำตัว / W หัว","≈ 0.54","ไม่รวมคอ; ภาพหน้าตรง")],"method":"Manual trace of assembled front view; circles are projected image shapes."},
    {"id":"shoulders", "step":"05", "name":"เบ้าไหล่", "viewBox":[550,271,174,46],
     "observation":"เป็นเบ้ากระบอกสั้น วงโครเมียมซ้อน 2–3 ชั้น ข้อต่ออยู่ชิดข้างลำตัวและเข้าถุงมือทันที ไม่มีลูกบอลใหญ่โผล่เหนือไหล่",
     "construction":"ทำข้างเดียวก่อน: เบ้าฝังลำตัว → วงรอยต่อดำบาง → ปลอกโครเมียมสั้น → โคนถุงมือ ล็อกแนวแกนเฉียงลงตามภาพหน้าตรง",
     "uncertainty":"จำนวนแหวนและรูปด้านในถูกบังบางส่วน ห้ามตีความแถบสะท้อนทุกเส้นเป็นชิ้นส่วนใหม่ ความหนาภายในเป็นส่วนที่ต้องออกแบบ",
     "paths":[path("shoulder-left","เบ้าฝั่งซ้ายในภาพ",SHOULDER_L,"#93ded0","#293f46"),path("shoulder-right","เบ้าฝั่งขวาในภาพ",SHOULDER_R,"#f2c788","#293f46"),
              path("socket-left","เบ้าติดลำตัวซ้าย","M 601 280 C 592 284 587 295 588 305","#c5aedf"),
              path("socket-right","เบ้าติดลำตัวขวา","M 675 280 C 684 284 689 295 688 305","#c5aedf"),
              path("axis-left","แนวเชื่อมซ้าย · ประมาณ","M 592 293 L 569 290","#9ebcf2"),path("axis-right","แนวเชื่อมขวา · ประมาณ","M 684 293 L 706 291","#9ebcf2")],
     "landmarks":[mark("โคนเบ้าซ้าย",589,294),mark("โคนเบ้าขวา",688,294)],
     "dimensions":[dim("ตรวจเป็นอันดับแรก","เบ้าชิดตัว","ใช้ช่องว่างที่เห็นจริง ไม่เพิ่มก้านแขนเพื่อให้ท่าง่ายขึ้น")],"method":"Manual outline of visible cuff surfaces; attachment axes are construction hypotheses."},
    {"id":"hands-rest", "step":"06", "name":"มือ · ท่าพัก", "viewBox":[530,280,212,77],
     "observation":"ถุงมือหนานุ่ม โคนนิ้วรวมกับฝ่ามือ มือพักหันเข้าข้างตัวและงอนิ้วเล็กน้อย จึงเห็นนิ้วซ้อนกัน",
     "construction":"ปั้นมือข้างเดียวในท่าพักให้ได้ silhouette นี้ก่อน รักษาฝ่ามืออวบ นิ้วมนและนิ้วโป้งแยกด้านใน จากนั้นค่อยวางโครงนิ้ว",
     "uncertainty":"เส้นภายในที่ถูกบังไม่ใช่ขอบนิ้วทั้งหมด ต้องดูร่วมกับท่าแบมือ ห้ามคัดลอกเงาดำเป็นร่องลึกถาวร",
     "paths":[path("hand-left","มือซ้ายในภาพ",HAND_L,"#93ded0","#e8ecef"),path("hand-right","มือขวาในภาพ",HAND_R,"#f2c788","#e8ecef")],
     "landmarks":[mark("นิ้วโป้งด้านใน",570,326),mark("นิ้วโป้งด้านใน",704,326)],
     "dimensions":[dim("ตรวจรูปมือ","พักแล้วไม่แบเข้ากล้อง","เปรียบเทียบโคนมือ ความอวบของฝ่ามือ และนิ้วซ้อนกัน")],"method":"Pixel contours at grayscale >150, polygon tolerance 0.65 px. Source ROIs left [532,286,579,352], right [695,286,739,352]. Top edge is the chosen split at the occluded cuff, not a recovered seam."},
    {"id":"hands-open", "step":"07", "name":"มือ · ท่าแบ", "viewBox":[23,505,161,191],
     "observation":"ภาพกางมือแสดงฝ่ามืออวบและนิ้วโป้งกาง นิ้วที่เหลือโค้งเข้าหาผู้ชม จึงดูสั้นจาก perspective",
     "construction":"ใช้ตรวจมือชิ้นเดียวกับท่าพัก ทั้งมวลฝ่ามือ ขนาดนิ้วและโคนถุงมือ การแบ/กำต้องรักษารูปทรง ไม่เปลี่ยนภาพมือ",
     "uncertainty":"จำนวนปลายนิ้วแต่ละภาพอ่านได้ไม่เท่ากัน ยังใช้ยืนยันโครงนิ้วทั้งหมดไม่ได้ นิ้วบางส่วนถูกบังและมี perspective จึงไม่ยืดตามความยาวฉายตรง ๆ",
     "paths":[path("hand-open","ขอบมือแบ",OPEN_HAND,"#93ded0","#e8ecef"),
              path("palm-mass","แนวฝ่ามือโดยประมาณ","M 98 563 C 75 573 63 594 69 614 C 79 637 114 644 141 625","#9ebcf2"),
              path("thumb-axis","แนวนิ้วโป้งโดยประมาณ","M 111 584 C 101 565 81 546 71 536","#f2c788")],
     "landmarks":[mark("นิ้วโป้ง",75,535),mark("ฝ่ามือ",114,607)],
     "dimensions":[dim("ตรวจคู่กับท่าพัก","มวลมือเดียวกัน","เส้นฝ่ามือและแนวนิ้วสีอื่นเป็นแนววางโครง ไม่ใช่ขอบจริง")],"method":"Manual visible silhouette; interior curves are proposed construction guides."},
    {"id":"halo", "step":"08", "name":"วงฐานแสง", "viewBox":[573,376,128,38],
     "observation":"วงขาวมีเนื้อและแกนสว่าง พร้อมแสงฟุ้งและเงานุ่ม วงรีที่เห็นเกิดจากมุมกล้องมองวงแหวนแนวนอน",
     "construction":"แยกเนื้อวงแหวนออกจากเอฟเฟกต์ฟุ้ง ล็อกขนาดเนื้อวงจากภาพหน้าตรงแล้วค่อยปรับแสง เพื่อไม่ให้ bloom หลอกสัดส่วน",
     "uncertainty":"ขอบนอกฟุ้งจึงไม่มีเส้นตัดแน่นอน ความหนาด้านข้างและระยะลอยต้องตรวจร่วมกับมุมเฉียง",
     "paths":[path("halo-core","เนื้อวงโดยประมาณ",RING,"#93ded0","#f7ffff")],
     "landmarks":[mark("แนวกลางวง",637,393)],
     "dimensions":[dim("กว้างเนื้อวง / W หัว","≈ 0.52–0.54","วัดแกนวงที่สว่าง ไม่รวมแสงฟุ้งด้านนอก")],"method":"Manual rounded contour calibrated against the bright ring at grayscale >=190 (source bbox x591–681, y383–402). Optical boundary, not a recovered solid cross-section."},
]


def export_svg(part, dest):
    def tag(name): return f"{{{SVG_NS}}}{name}"
    svg=ET.Element(tag("svg"),{"viewBox":" ".join(map(str,part["viewBox"])),"role":"img"})
    ET.SubElement(svg,tag("title")).text=f"Emma — {part['name']} / source-space draft"
    ET.SubElement(svg,tag("desc")).text=part["method"]+" No depth, rig or material approval is implied."
    for p in part["paths"]:
        ET.SubElement(svg,tag("path"),{"id":p["id"],"d":p["d"],"fill":p["fill"],
            "stroke":p["color"],"stroke-width":str(part["viewBox"][2]/500),
            "stroke-linejoin":"round","stroke-linecap":"round","fill-rule":"evenodd",
            "stroke-dasharray":str(part["viewBox"][2]/125) if p["guide"] else "none"})
    ET.ElementTree(svg).write(dest,encoding="utf-8",xml_declaration=True)


def main():
    original=SOURCE.read_bytes()
    source_hash=hashlib.sha256(original).hexdigest()
    if source_hash!=SOURCE_SHA256 or struct.unpack(">II",original[16:24])!=(1501,1008):
        raise ValueError("Reference image changed; recheck source-space contours before exporting.")
    OUT.mkdir(exist_ok=True)
    (OUT/"parts").mkdir(exist_ok=True)
    data={"source":"../emma/emma-pose-library-reference-v1.png","sourceSize":[1501,1008],
          "sourceSha256":source_hash,"status":"draft",
          "intro":"แกะเส้นขอบจากภาพต้นแบบเดิม แยกชิ้นที่มองเห็นและจุดที่ต้องออกแบบต่อ ก่อนขึ้นโมเดลทีละส่วน",
          "coordinateConvention":"Source image pixels; crop changes viewport only, never stretches the source.",
          "parts":PARTS,"views":[
              {"id":"front","name":"หน้าตรง · สัดส่วนรวม","viewBox":[533,102,204,310],"note":"ใช้ล็อกขนาดหัว ลำตัว ตำแหน่งตา ไหล่และวงฐาน"},
              {"id":"quarter","name":"มุม 3/4 · ความลึก","viewBox":[744,103,158,310],"note":"ใช้ดูชั้นเปลือกหัวและเบ้าไหล่ ภาพนี้ไม่ใช่มุมข้าง 90°"},
              {"id":"back","name":"ด้านหลัง · ฝาครอบ","viewBox":[905,102,183,311],"note":"เห็นแผ่นขาวหลังหัวและฝาหลังลำตัว รายละเอียดด้านใต้ยังไม่ปรากฏ"}
          ]}
    (OUT/"reference-map.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for part in PARTS: export_svg(part,OUT/"parts"/(part["id"]+".svg"))
    print(json.dumps({"parts":len(PARTS),"paths":sum(len(p["paths"]) for p in PARTS),"source_sha256":data["sourceSha256"]}))


if __name__=="__main__": main()
