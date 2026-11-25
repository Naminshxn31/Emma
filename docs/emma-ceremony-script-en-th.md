# Emma Ceremony — paired script: English (spoken) + Thai (subtitle)

> **For the motion-presentation team.** Emma's **voice is English**. The **Thai column is the subtitle** for the bottom of your screen — Emma's own page draws no subtitles. The durations are of **preview takes** and will be measured again once the owner has approved the lines; leave each subtitle up until she stops rather than cutting on the clock. The demo-room line in scene 5 has **no words yet** (it waits for the room number from sales).

Generated from `data/ceremony/floor1_ceremony.json` by `python scripts/export_ceremony_subtitles.py`, which also writes the same rows to `data/ceremony/floor1_ceremony.subtitles.csv` (UTF-8 with BOM, opens in Excel). Never edit either by hand: change the line in the ceremony file and run the script again.

## How to read it

- Rows are in the order staff press them on the ceremony page. The page shows no step numbers: a row is named by its **scene** and the **step's label as the page shows it**, and a line also by its **cue id**.
- **Emma says (EN)** is read word for word. **Subtitle (TH)** is the Thai line, unchanged.
- **Seconds** = length of Emma's rendered voice for that line, silence trimmed at both ends. Her reading on the day can differ by about 15%.
- Total speech: **251 s over the 28 lines in the running order** (+ the backup line, pressed by hand only).
- **SHOW TEAM** rows are not Emma's: the LED, the videos, the lights, the curtain, the globe, the box. Staff tick them on the page once they have really happened; Emma's "…เรียบร้อยแล้ว" line always comes after its tick.
- Numbers are the approved ones: the beach is **about 600 metres in a straight line**, the indoor facilities **around 2,500 square metres (the sales team's estimate)**. The LED must show the same.
- A line is said on stage only once the owner has approved it (`scripts/approve_event_script.py`); a draft is never said.

## Scene 1 — วิสัยทัศน์และการเปิดตัวเอ็มม่า

| On the page | Who | Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |
|---|---|---|---|---|---|---|
| ไฟห้องปิด ม่านปิด หุ่นพร้อมในกล่อง | **SHOW TEAM** | — | — | — | — | — |
| เปิดตัว (การ์ด 1) | Staff | — | — | — | — | — |

## Scene 2 — เอ็มม่าช่วยอะไรได้และโครงการแตกต่างอย่างไร

| On the page | Who | Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |
|---|---|---|---|---|---|---|
| Emma: s02_l03 | Emma | `s02_l03` | สวัสดีเอ็มม่า ยินดีต้อนรับนะครับ ทุกคนคงอยากรู้แล้วว่า คุณสามารถช่วยพวกเรา หรือทำอะไรได้บ้าง? | Hello, Khun Miki, and hello, everyone. I'm Emma. I represent Embassy World, and I'm the smart assistant for our sales team and all of our agents. | สวัสดีค่ะคุณมิกิ และสวัสดีทุกท่านค่ะ เอ็มม่าเป็นตัวแทนของ Embassy World และเป็นผู้ช่วยอัจฉริยะสำหรับทีมขายและเอเจนต์ทุกท่านค่ะ | 10.9 |
| Emma: s02_l05 | Emma | `s02_l05` | (ต่อจากบทก่อน — จอแสดงภาพความสามารถของเอ็มม่าทีละเรื่อง) | I can share project information, recommend unit types to match each customer's needs, show floor plans and visualisations, and help prepare quotation details, so the sales team can look after customers more easily. I'd be glad to help everyone get to know Embassy World better. Khun Miki, please go ahead and ask. | เอ็มม่าช่วยให้ข้อมูลโครงการ แนะนำแบบห้องตามความต้องการของลูกค้า แสดงแปลนและภาพจำลอง รวมถึงช่วยจัดเตรียมข้อมูลใบเสนอราคา เพื่อส่งต่อให้ทีมขายดูแลลูกค้าได้สะดวกขึ้นค่ะ เอ็มม่ายินดีที่จะช่วยให้ทุกท่านรู้จัก Embassy World มากขึ้น คุณมิกิเริ่มถามได้เลยค่ะ | 18.8 |
| Emma: s02_l09 | Emma | `s02_l09` | ถ้าอย่างนั้น ก่อนที่เราจะลงรายละเอียด เอ็มม่าช่วยบอกทุกคนหน่อยครับว่า โครงการของเราแตกต่างจากที่อื่นอย่างไร? | What makes Embassy World distinctive is that it brings together futuristic design, an atmosphere made for relaxing, and technology that makes everyday living easier, all in a single project. We care about both the living experience and how the project is looked after, including a Smart Home concept that lets residents control their devices more easily. So Embassy World is a project for people who welcome innovation, look for something new, and want to live in a modern environment. | สิ่งที่ทำให้ Embassy World มีเอกลักษณ์ คือการนำงานออกแบบแห่งอนาคต บรรยากาศสำหรับการพักผ่อน และเทคโนโลยีที่ช่วยให้การใช้ชีวิตสะดวกขึ้น มารวมไว้ในโครงการเดียวค่ะ เราให้ความสำคัญกับทั้งประสบการณ์การอยู่อาศัยและการดูแลโครงการ รวมถึงแนวคิด Smart Home ที่ให้ผู้อยู่อาศัยควบคุมอุปกรณ์ได้สะดวกขึ้นค่ะ Embassy World จึงเป็นโครงการสำหรับคนที่เปิดรับนวัตกรรม มองหาสิ่งใหม่ และอยากใช้ชีวิตในสภาพแวดล้อมที่ทันสมัยค่ะ | 30.9 |

## Scene 3 — ทำเลที่ตั้ง

| On the page | Who | Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |
|---|---|---|---|---|---|---|
| Emma: s03_l03 | Emma | `s03_l03` | เอาล่ะครับ ตอนนี้ทุกคนคงเห็นแล้วว่า Embassy World มีอะไรพิเศษบ้าง แต่ผมยังอยากรู้อีกเรื่อง... โครงการของเราตั้งอยู่ที่ไหนครับเอ็มม่า? | Of course. Embassy World is in the Jomtien Second Road area, about 600 metres from Jomtien Beach in a straight line. Around the project you'll find plenty of conveniences: attractions, shopping, hospitals and schools, and getting around is easy for everyday life in Pattaya. | ได้เลยค่ะ Embassy World ตั้งอยู่ย่านถนนจอมเทียนสายสอง ห่างจากหาดจอมเทียนเป็นเส้นตรงประมาณ 600 เมตรค่ะ รอบโครงการมีสิ่งอำนวยความสะดวกหลากหลาย ทั้งสถานที่ท่องเที่ยว แหล่งช้อปปิ้ง โรงพยาบาล และโรงเรียน รวมถึงการเดินทางที่สะดวกต่อการใช้ชีวิตในพัทยาค่ะ | 17.6 |

## Scene 4 — ห้องพักและสิ่งอำนวยความสะดวก

| On the page | Who | Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |
|---|---|---|---|---|---|---|
| Emma: s04_l03 | Emma | `s04_l03` | เริ่มจากห้องพักก่อนเลยครับ โครงการของเรามีทั้งหมดกี่ยูนิต และมีห้องแบบไหนบ้าง? | Embassy World has 1,066 units in total: studios, 1-bedroom units and 2-bedroom units, to suit different ways of living. | Embassy World มีทั้งหมด 1,066 ยูนิตค่ะ มีทั้งห้องสตูดิโอ แบบ 1 ห้องนอน และแบบ 2 ห้องนอน เพื่อรองรับรูปแบบการใช้ชีวิตที่แตกต่างกันค่ะ | 8.7 |
| Emma: s04_l06 | Emma | `s04_l06` | แล้วสิ่งอำนวยความสะดวกของเรา มีอะไรที่เอ็มม่าอยากให้ทุกคนเห็นเป็นพิเศษครับ? | Our main common areas are in Building A: on the basement level, the ground floor and floor 3, as well as outdoors. A real highlight is the swimming pool, a full 155 metres long. There are several more places to relax, too. Khun Miki, is there an area you'd like me to show you in particular? | พื้นที่ส่วนกลางหลักของเราอยู่ที่อาคาร A ค่ะ มีทั้งบริเวณชั้นใต้ดิน ชั้นล่าง และชั้น 3 รวมถึงพื้นที่ภายนอกอาคาร และหนึ่งในไฮไลต์ คือสระว่ายน้ำความยาวถึง 155 เมตรค่ะ นอกจากนี้ยังมีพื้นที่พักผ่อนอีกหลายส่วน คุณมิกิอยากให้เอ็มม่าพาชมส่วนไหนเป็นพิเศษไหมคะ? | 18.1 |
| Emma: s04_l09 | Emma | `s04_l09` | ว้าว... คุณจำรายละเอียดได้ดีมากเลยนะเอ็มม่า | Thank you, Khun Miki. I've studied this project very carefully. There's just a single thing I still haven't tried... | ขอบคุณค่ะคุณมิกิ เรื่องโครงการนี้เอ็มม่าศึกษามาอย่างดีแล้วค่ะ จะเหลือก็แค่อย่างเดียวที่ยังไม่เคยลองค่ะ... | 8.2 |
| Emma: s04_l11 | Emma | `s04_l11` | (ต่อจากบทเกริ่นทันที ไม่เว้นจังหวะ — จอขึ้นภาพสระว่ายน้ำ) | Swimming in the 155-metre pool myself! | ลงไปว่ายน้ำในสระ 155 เมตรด้วยตัวเองค่ะ! | 4.6 |
| Emma: s04_l13 | Emma | `s04_l13` | (พักรับเสียงหัวเราะ) | The day I'm completely waterproof, I'd love to go and show off a few laps. | ถ้าวันไหนเอ็มม่ากันน้ำได้ร้อยเปอร์เซ็นต์ เอ็มม่าจะขอไปว่ายโชว์บ้างนะคะ | 5.5 |
| Emma: s04_l15 | Emma | `s04_l15` | ได้เลยครับ แต่วันนี้อยู่ช่วยทีมขายกับผมก่อนนะเอ็มม่า | With pleasure, Khun Miki! | ด้วยความยินดีค่ะคุณมิกิ! | 2.3 |

## Scene 5 — แนะนำห้องและใบเสนอราคา

| On the page | Who | Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |
|---|---|---|---|---|---|---|
| Emma: s05_l03 | Emma | `s05_l03` | ถ้าอย่างนั้น เรามาลองสถานการณ์จริงกันครับ สมมติว่าผมเป็นลูกค้าที่กำลังมองหาห้องสำหรับครอบครัว อยู่กับภรรยาและลูก ๆ เอ็มม่าจะช่วยแนะนำอย่างไร? | Of course, Khun Miki. To begin, what kind of view do you like? Would you enjoy a view of the sea, are you interested in a ground-floor unit on the side facing the pool, or do you prefer a view over the common areas and the project's swimming pool? | ได้เลยค่ะคุณมิกิ ก่อนอื่นอยากทราบว่าคุณมิกิชอบวิวแบบไหนคะ? อยากชมวิวทะเล สนใจห้องชั้น G ฝั่งสระว่ายน้ำ หรือชอบวิวพื้นที่ส่วนกลางและสระว่ายน้ำของโครงการคะ? | 14.5 |
| Emma: s05_l06 | Emma | `s05_l06` | ผมอยากได้ห้องที่มองเห็นทะเลครับ | In that case, I'd suggest starting with a 2-bedroom unit with a view of the sea, so everyone in the family has room to live and some privacy. | ถ้าอย่างนั้น เอ็มม่าแนะนำให้เริ่มพิจารณาห้องแบบ 2 ห้องนอนวิวทะเลค่ะ เพื่อให้สมาชิกในครอบครัวมีพื้นที่ใช้งานและความเป็นส่วนตัวค่ะ | 8.7 |
| Emma: s05_l08 | Emma | `s05_l08` | (ต่อจากบทก่อน) | Khun Miki, would you like me to prepare a quotation for this unit as well? | คุณมิกิต้องการให้เอ็มม่าจัดเตรียมใบเสนอราคาสำหรับห้องนี้ด้วยไหมคะ? | 4.2 |
| Emma: s05_l11 | Emma | `s05_l11` | ดีเลยครับ ช่วยพิมพ์ใบเสนอราคา แล้วส่งต่อให้เจ้าหน้าที่ที่ดูแลผมด้วยครับ | Certainly, Khun Miki. I'm preparing the quotation for this unit now. | ได้ค่ะคุณมิกิ เอ็มม่ากำลังจัดเตรียมใบเสนอราคาสำหรับห้องนี้ค่ะ | 4.4 |
| Emma: s05_l13 | Emma | `s05_l13` | (เจ้าหน้าที่รับใบเสนอราคาและนำมามอบให้คุณมิกิ) | Our staff will bring the details to you, Khun Miki, and explain the next steps. | เจ้าหน้าที่จะนำรายละเอียดมามอบให้คุณมิกิ และช่วยอธิบายขั้นตอนต่อไปค่ะ | 4.5 |
| ใบเสนอราคา — เจ้าหน้าที่มอบเอกสารให้คุณมิกิ | Staff | — | — | — | — | — |

## Scene 6 — ข้อมูลเชิงลึกของโครงการ

| On the page | Who | Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |
|---|---|---|---|---|---|---|
| Emma: s06_l03 | Emma | `s06_l03` | แล้วถ้าลูกค้าถามรายละเอียดลึกกว่านั้นล่ะครับเอ็มม่า อย่างความสูงของอาคาร ความลึกของสระว่ายน้ำ หรือขนาดพื้นที่ส่วนกลาง คุณตอบได้ไหม? | Certainly, Khun Miki. I have that information ready. The project's main swimming pool is 1.3 metres deep. The building is 8 storeys high, the roof level is about 23 metres above road level, and the sales team estimates the indoor facilities at around 2,500 square metres in total. | ได้ค่ะคุณมิกิ ข้อมูลส่วนนี้เอ็มม่าเตรียมไว้แล้วค่ะ สระว่ายน้ำหลักของโครงการมีความลึก 1.3 เมตร อาคารสูง 8 ชั้น พื้นดาดฟ้าสูงประมาณ 23 เมตรจากระดับถนน และทีมขายประมาณว่า Facility ในร่มมีพื้นที่รวมราว 2,500 ตารางเมตรค่ะ | 19.8 |
| Emma: s06_l05 | Emma | `s06_l05` | ถูกต้องครับเอ็มม่า คุณรู้รายละเอียดดีมากจริง ๆ | Absolutely, Khun Miki. Besides my good looks, I've studied everything about Embassy World very carefully. | แน่นอนค่ะคุณมิกิ นอกจากหน้าตาที่ดูดีแล้ว เรื่องข้อมูล Embassy World เอ็มม่าก็ตั้งใจศึกษามาอย่างดีค่ะ | 6.5 |
| Emma: s06_l07 | Emma | `s06_l07` | (ต่อจากบทเกริ่นทันที ไม่เว้นจังหวะ — จอค้างภาพสรุปข้อมูล) | If there's ever an Embassy World superfan quiz... I'm sure I'd take first place! | ถ้ามีการจัดประกวดแฟนพันธุ์แท้ Embassy World เมื่อไร... เอ็มม่ามั่นใจว่าจะคว้าที่หนึ่งมาได้แน่นอนค่ะ! | 5.6 |

## Scene 7 — เทคโนโลยีของโครงการ

| On the page | Who | Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |
|---|---|---|---|---|---|---|
| Emma: s07_l02 | Emma | `s07_l02` | อีกสิ่งหนึ่งที่ผมอยากให้ทุกคนเห็น คือเทคโนโลยีที่เราตั้งใจนำมาใช้ในการอยู่อาศัยและการดูแลโครงการครับ เอ็มม่า ช่วยเปิดวิดีโอเทคโนโลยีของ Embassy World ให้ทุกท่านชมหน่อยครับ | Of course, Khun Miki. Everyone, please enjoy the video. | ได้เลยค่ะคุณมิกิ ขอเชิญทุกท่านรับชมค่ะ | 3.9 |
| วิดีโอเทคโนโลยี (ทีมจอ LED เล่น) | **SHOW TEAM** | — | — | — | — | — |
| หน้าถัดไป: แดชบอร์ดคุณภาพน้ำ (ทีมจอ LED เปลี่ยน) | **SHOW TEAM** | — | — | — | — | — |
| Emma: s07_l07 | Emma | `s07_l07` | หน้านี้แสดงอะไร และช่วยทีมดูแลโครงการอย่างไรครับ? | This page is the water-quality dashboard. On a single screen, the team looking after the project can see the information the system shows, and use it to help monitor the project's water quality. | หน้านี้คือแดชบอร์ดแสดงข้อมูลคุณภาพน้ำค่ะ ช่วยให้ทีมดูแลโครงการเห็นข้อมูลที่ระบบแสดงไว้บนหน้าจอเดียว และใช้ข้อมูลเหล่านี้ประกอบการติดตามดูแลคุณภาพน้ำของโครงการค่ะ | 13.4 |

## Scene 8 — สาธิตระบบ Smart Home

| On the page | Who | Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |
|---|---|---|---|---|---|---|
| Emma: s08_l06 | Emma | `s08_l06` | วันนี้ผมอยากให้ทุกท่านได้เห็นการทำงานของระบบด้วยตัวเอง เรามาเริ่มจากไฟในล็อบบี้กันครับ เอ็มม่า ช่วยปิดไฟล็อบบี้ครับ | Certainly, Khun Miki. Turning off the lobby lights now. | ได้ค่ะคุณมิกิ กำลังปิดไฟล็อบบี้ค่ะ | 3.8 |
| ไฟล็อบบี้ดับแล้ว — ทีมไฟยืนยัน | **SHOW TEAM** | — | — | — | — | — |
| Emma: s08_l08 | Emma | `s08_l08` | (ไฟบริเวณสาธิตดับลง) | The lobby lights are off. | ปิดไฟล็อบบี้เรียบร้อยแล้วค่ะ | 1.9 |
| Emma: s08_l10 | Emma | `s08_l10` | เอ็มม่า เปิดไฟล็อบบี้อีกครั้งครับ | Certainly. Turning the lobby lights on now. | ได้ค่ะ กำลังเปิดไฟล็อบบี้ค่ะ | 2.9 |
| ไฟล็อบบี้สว่างแล้ว — ทีมไฟยืนยัน | **SHOW TEAM** | — | — | — | — | — |
| Emma: s08_l12 | Emma | `s08_l12` | (ไฟกลับมาสว่าง) | The lobby lights are on. | เปิดไฟล็อบบี้เรียบร้อยแล้วค่ะ | 2.1 |
| Emma: s08_l14 | Emma | `s08_l14` | แล้วผ้าม่านล่ะครับ เอ็มม่า ช่วยเปิดผ้าม่านล็อบบี้ด้วยครับ | Of course, Khun Miki. Opening the lobby curtains now. | ได้เลยค่ะคุณมิกิ กำลังเปิดผ้าม่านล็อบบี้ค่ะ | 3.9 |
| ผ้าม่านเปิดเสร็จ — ทีมงานยืนยัน | **SHOW TEAM** | — | — | — | — | — |
| Emma: s08_l16 | Emma | `s08_l16` | (ผ้าม่านเปิดเสร็จ) | The curtains are open. | เปิดผ้าม่านเรียบร้อยแล้วค่ะ | 2.0 |

## Scene 9 — The Story และคำขอบคุณจากคุณมิกิ

| On the page | Who | Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |
|---|---|---|---|---|---|---|
| Emma: s09_l02 | Emma | `s09_l02` | ทุกอย่างที่ทุกท่านเห็นในวันนี้ เป็นส่วนหนึ่งของความตั้งใจที่เราใส่ลงไปใน Embassy World ครับ ก่อนเปิดพรีวิวอย่างเป็นทางการ ผมอยากให้ทุกท่านได้ชมภาพโครงการและเรื่องราวที่เราอยากถ่ายทอดผ่านงานออกแบบนี้ เอ็มม่า เปิดวิดีโอ Embassy World Story ครับ | With pleasure. Everyone, please enjoy the story of Embassy World. | ด้วยความยินดีค่ะ ขอเชิญทุกท่านรับชมเรื่องราวของ Embassy World ค่ะ | 4.7 |
| วิดีโอ Embassy World Story (ทีมจอ LED เล่น) | **SHOW TEAM** | — | — | — | — | — |

## Scene 10 — เปิดพรีวิวอย่างเป็นทางการ

| On the page | Who | Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |
|---|---|---|---|---|---|---|
| ไปยืนข้างลูกโลก LED | Emma walks | — | — | — | — | — |
| คุณมิกิแตะลูกโลก → Fantasy + ไฮไลต์โครงการ (ทีมจอ LED เล่น) | **SHOW TEAM** | — | — | — | — | — |
| Emma: s10_l05 | Emma | `s10_l05` | (วิดีโอ Fantasy จบ จอค้างภาพโครงการและโลโก้ ดนตรีเบาลง) | Thank you all for being part of Embassy World today. I'm delighted to be an assistant to our sales team and all of our agents, and I'm ready to help customers get to know our project even better. Please take a look at the project details, talk with our team, and try the Smart Home experience for yourselves. | ขอบคุณทุกท่านที่มาร่วมเป็นส่วนหนึ่งของ Embassy World ในวันนี้ค่ะ เอ็มม่ายินดีที่จะเป็นผู้ช่วยให้กับทีมขายและเอเจนต์ทุกท่าน และพร้อมช่วยให้ลูกค้าได้รู้จักโครงการของเรามากยิ่งขึ้นค่ะ ขอเชิญทุกท่านชมรายละเอียดโครงการ พูดคุยกับทีมงาน และทดลองประสบการณ์ Smart Home ได้เลยนะคะ | 18.7 |
| จบพิธี → เปิดแอป Emma Event | Emma → Emma Event app | — | — | — | — | — |

## Pressed by hand only (not in the running order)

| Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |
|---|---|---|---|---|
| `s05_backup_quote` | ช่วยแสดงตัวอย่างใบเสนอราคาของห้องนี้ให้ชมหน่อยครับ | Certainly. Here is a sample quotation for this unit. Our staff will explain the details. | ได้ค่ะ นี่คือตัวอย่างใบเสนอราคาของห้องนี้ เจ้าหน้าที่จะช่วยอธิบายรายละเอียดต่อค่ะ | 6.4 |

