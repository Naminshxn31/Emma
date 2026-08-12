# Condo Voice Assistant

A ChatGPT-style voice assistant for a condo sales gallery robot: pick a
voice, then just talk. Natural-sounding speech, interruptible mid-sentence,
Thai and English without switching modes.

Runs on **Gemini Live (free tier)** by default, or OpenAI Realtime if you
want to compare. Same UI either way — one line in `.env` switches provider.

## How this differs from a normal voice bot

Most voice assistants are a **cascade**: speech → text → LLM → text → speech.
Every hop loses information (tone, emotion, emphasis) and adds latency, which
is why they sound robotic and can't be interrupted naturally.

This is **speech-to-speech**: one model hears the guest's audio and produces
its own speech directly.

```
guest speaks → [ speech-to-speech model ] → robot speaks
                (hears audio, emits audio — no text round-trip)
```

Transcripts still appear on screen, but they're a side output for the UI, not
a stage in the pipeline.

- **Sounds human.** Tone and pacing survive, because they were never
  flattened into text.
- **Interruptible.** Talk over the robot and it stops immediately.
- **Multilingual with no mode switch.** Reply language follows whatever the
  guest just spoke — Gemini's native-audio models cover ~97 languages and
  switch mid-conversation on their own. See `REPLY_LANGUAGES` below.
- **Hears *how* something was said** — hesitation, emphasis, mood.

## Providers

| | **Gemini Live** (default) | **OpenAI Realtime** |
| --- | --- | --- |
| Cost | **Free tier available** | Paid per audio minute |
| Model (default) | `gemini-2.5-flash-native-audio-preview-12-2025` | `gpt-realtime-2.1` |
| Voices | 30 | 10 |
| Mic audio | PCM16 **16 kHz** | PCM16 **24 kHz** |
| Reply audio | PCM16 24 kHz | PCM16 24 kHz |
| Session cap | 15 min | 60 min |
| Interruption | server drops unsent audio; client just stops | client must also report how much was heard |
| Extras | affective dialogue, proactive audio (2.5 only) | semantic VAD |
| Key | `GEMINI_API_KEY` ([free](https://aistudio.google.com/apikey)) | `OPENAI_API_KEY` |

**The gallery deployment runs `gemini-3.1-flash-live-preview`, set in `.env`.**
The table above is the *default* in `app/config.py`, which is the older 2.5
native-audio model. The two are not interchangeable and the difference is not
cosmetic: 3.1 does **not** support affective dialogue, proactive audio, or
NON_BLOCKING function calling, and `GEMINI_AFFECTIVE_DIALOG=true` is ignored
with a warning on it. Anything read here about those features applies to 2.5.

`GEMINI_MODEL_FALLBACK` switches models by itself if the configured one
cannot be opened — a preview model can be withdrawn, renamed or rate-limited
with little notice, and on that day the robot goes silent for a whole day with
a traceback nobody is watching. It fires **only** for errors that name the
model (404, not found, quota). A dropped connection still fails loudly:
falling back on any error turns a five-second outage into a permanent silent
downgrade that nobody investigates, because the gallery keeps working.

`app/providers/` hides all of that from the rest of the app — the browser
speaks one protocol and learns the sample rates at runtime from the `ready`
event.

## Architecture

```
browser  <--normalized WS-->  this server  <--SDK / WS-->  Gemini | OpenAI
 (mic, speakers,               (app/session.py
  voice picker)                 + app/providers/)
```

The server is a **proxy, not a passthrough**:

1. The API key never reaches the browser.
2. Condo instructions are injected server-side, so a guest can't rewrite the
   assistant's rules from devtools (there's a test for this).
3. The browser doesn't care which provider is active.

Reply audio crosses the browser link as **binary frames**, not base64 JSON —
about a third fewer bytes and no encode/decode per chunk.

| File | Role |
| --- | --- |
| `app/session.py` | Browser WebSocket, normalized protocol, both relay pumps |
| `app/providers/base.py` | Provider interface + the protocol spec |
| `app/providers/gemini.py` | Gemini Live |
| `app/providers/openai_realtime.py` | OpenAI Realtime |
| `app/prompts.py` | System instructions; facts loaded from `data/condo_facts.json` |
| `app/voices.py` | Voice catalogues per provider |
| `app/tools/` | The 19 callable tools, the search, and the Canva window |
| `app/turnlog.py` | One JSON line per turn to `data/logs/`, deleted after 30 days |
| `client/index.html` | Voice picker, live call UI, mic capture, playback, barge-in |
| `CLAUDE.md` | Every bug that reached a guest, and why each fix is shaped the way it is |

## Setup

Plain Python — runs natively on Windows. **You do not need WSL.** If a
terminal offers to install Windows Subsystem for Linux, you're in the wrong
shell: press Ctrl+C and open PowerShell instead. (In VS Code: the `v` arrow
next to `+` in the terminal panel → PowerShell.)

**Windows (PowerShell)**

```powershell
cd C:\path\to\condo-voice
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

If PowerShell blocks the activate script with an execution-policy error, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then retry.

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Get a **free** Gemini key at
[aistudio.google.com/apikey](https://aistudio.google.com/apikey) and put it in
`.env`:

```
VOICE_PROVIDER=gemini
GEMINI_API_KEY=AIza...
PROJECT_NAME=ชื่อโครงการของคุณ
```

Run it:

```
uvicorn app.main:app --port 8000
```

Open `http://localhost:8000/` in Chrome or Edge, pick a voice, press **Start
conversation**. The robot greets you first, then just talk — no button to
hold, and you can cut it off mid-sentence.

Use `localhost`, not a `file://` path or a LAN IP — browsers only grant
microphone access on `localhost` or HTTPS.

### When the transcript doesn't match what was said

The guest's bubble showing "bit fire" when they said "ปิดไฟ" — while the
lights correctly go off — is not a bug in the assistant. It's the clearest
demonstration of how this architecture works:

```
audio → model → action + speech     ← understood correctly
audio → transcriber → text          ← a separate, weaker pipeline
```

The model never reads that text. So a mangled caption means the *caption* is
wrong, not the understanding. Judge whether it heard you by what it does,
not by what the bubble says.

You can improve the caption, though:

- `TRANSCRIBE_LANGUAGES` (default `th-TH,en-US,zh-CN`) biases the recogniser.
  These are **hints, not a whitelist** — detection still runs, it's just
  weighted toward what's listed, and the reply language is unaffected either
  way. The default is not `auto` because unbiased detection kept decoding
  Thai speech with an English recogniser and returning romanised nonsense
  ("ao rummy"), which reads as the robot mishearing when it hadn't. Add a
  code (`ru-RU`, `ja-JP`, `ko-KR`) if a language is captioned badly; set
  `auto` to drop the hints entirely.
- `app/providers/gemini.py`'s `adaptation_phrases()` feeds the project name,
  the hardware commands and sales vocabulary in as `custom_vocabulary`.
  Naming a word doesn't restrict which languages are recognised, it just
  stops that word becoming a homophone. Add terms specific to your project
  there — unit names, facility names, anything being misheard.

Note that `language_hints`, `language_auto` and `adaptation_phrases` are all
deprecated in google-genai 2.16 in favour of top-level `language_codes` and
`custom_vocabulary`. The current names are sent first; the old ones are only
a fallback for older SDKs.

These use `language_hints` / `adaptation_phrases`, which the SDK supports but
the public Live API reference doesn't document. If a future version rejects
them the code falls back to plain transcription rather than dropping the
session.

### The transcript panel

The call screen shows the conversation as chat bubbles that fill in **live**
while each side is still speaking, rather than appearing only once a turn
ends. Useful in a gallery because a guest can read back what the robot
understood — Thai speech recognition is not perfect, and seeing a
misheard word explains a wrong answer immediately.

- A reply the guest talked over is closed off and tagged
  *ถูกพูดแทรก · interrupted*, so the transcript never implies the whole
  answer was heard.
- **Copy** puts the whole conversation on the clipboard as
  `ลูกค้า:` / `ผู้ช่วย:` lines — handy for pasting into lead notes.
- Auto-scroll pauses if you scroll up to re-read something.

### Switching to OpenAI

```
VOICE_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Restart uvicorn. The picker swaps to OpenAI's 10 voices automatically.

## Languages

`REPLY_LANGUAGES=auto` (the default) means the assistant answers in whatever
language the guest spoke, switching mid-conversation without being asked. The
model does the work here — nothing in this project detects or translates.

To restrict it, list the codes you want, first one being what it opens with:

```
REPLY_LANGUAGES=th,en,zh
```

Worth restricting only if your sales staff can't follow up in the other
languages — an assistant that answers a Russian buyer fluently and then hands
them to someone who can't is worse than one that says so upfront.

If it starts refusing a language it should handle, that restriction is coming
from `REPLY_LANGUAGES` or from wording you added to `app/prompts.py`, not from
the model.

## Tools — things it can actually do

The assistant controls the gallery lights and air conditioner by calling
functions, not by describing them. Say "ปิดไฟหน่อย" or "ร้อนจัง" and it
invokes the tool and confirms in whatever language you're speaking.

Nineteen of them, in five groups. Every result carries what actually
happened — `hardware: "ok" / "mock" / "failed"` — so the robot can say a
command didn't get through instead of cheerfully claiming success. That field
exists because an earlier version reported "mock" as "ok" and the assistant
announced it had switched off an air conditioner that never received anything.

| Group | Tools |
| --- | --- |
| Room (`smarthome.py`, IR via Broadlink) | `set_lights`, `set_air_conditioner`, `get_room_status` |
| Slides (`slides.py`) | `show_slide`, `start_presentation`, `next_slide`, `previous_slide`, `go_to_page`, `hide_slide`, `stop_presentation`, `resume_presentation`, `close_presentation` |
| Knowledge (`knowledge.py`) | `search_condo_info` |
| Printing (`documents.py`) | `print_document`, `list_documents` |
| Robot (`robot.py`, Aobo SDK) | `go_to_place`, `stop_moving`, `return_to_base`, `get_robot_status` |

The robot group talks to an Android app on the robot down the WebSocket that
is already open for voice — the SDK is Android-only and reaches the robot's
navigation board on its own internal network, so this server can't call it.
Mock until that app reports in. See `docs/ต่อกับหุ่นยนต์ Astronaut.md`.

## Presenting slides

Slides appear **inline in the conversation window** as soon as one is shown,
so you can see what the guest is seeing without a second monitor.

For the real thing, open **`http://localhost:8000/display`** in a second
window — press `f` for fullscreen, or click **เปิดจอเต็ม ↗** on the inline
preview. On the robot this is the 18.5" chest screen. It reconnects on its
own and syncs to whatever is currently showing, so a screen switched on
mid-presentation isn't blank.

If no fullscreen display is connected, the conversation window says so once
per call. That used to be silent: the tools reported success, `/display`
wasn't open, and the presentation went nowhere visible.

Two modes, both driven by the model calling tools:

- **Answer with a slide.** "ขอดูผังห้อง" → `show_slide` finds it and it
  appears while the assistant talks about it.
- **Narrated tour.** "แนะนำโครงการหน่อย" → `start_presentation` starts an
  ordered deck; the model narrates a slide, calls `next_slide` itself, and
  keeps going. Interrupting just works — it's a normal conversation, not a
  mode the robot has to be pulled out of.

Tours are defined in `TOURS` in `app/tools/slides.py`: `overview`,
`facilities`, `floorplans`, `location`.

### Your own deck

Canva "view" links can't be read programmatically. Export from Canva as
**PDF** (Share → Download → PDF Standard), then:

```powershell
pip install pymupdf
python scripts/import_slides.py "Embassy World Present V.3.pdf"
```

Each page becomes a slide, and any text on the page seeds the title and
keywords. **Then open `data/slides/index.json` and fix the titles and
keywords for the slides that matter** — search is what decides whether the
assistant can find a slide, and auto-extracted text is a starting point, not
a finished index. A slide it can't find is one it will never show.

`--append` keeps the existing library (85 slides ported from `emma`,
already catalogued in Thai and English) and adds yours after it.

### Mirroring the real Canva window

The images above are a one-time export — the assistant can't reach back into
Canva to show the design "live." **Embedding it can't either**: a Canva view
link sets headers that refuse to load inside an `<iframe>` on any other
page. Tried it by hand — an embedded `.../view?embed` renders nothing, no
console error, the frame is just empty.

What Canva doesn't block is opening that same link as its own real,
top-level browser window and driving it with automation — that's not
embedding, it's the same thing a person clicking through it would do. Set
`CANVA_URL` to the deck's view link and every slide change also sends a real
Chromium window (via Playwright) to the matching page, deep-linked by
fragment (`.../view#42` jumps straight to page 42 — confirmed this works
without visiting 1..41 first). `app/tools/canva_display.py` owns this; it's
entirely opt-in and `playwright` is only imported if `CANVA_URL` is set.

```powershell
pip install playwright
playwright install chromium
```

```
CANVA_URL=https://www.canva.com/design/XXXX/YYYY/view
CANVA_DECK_PREFIX=ew      # id prefix the deck was imported under
CANVA_HEADLESS=false      # a real window, for an actual display
CANVA_KIOSK=true          # fullscreen, no browser chrome
```

Only slides from the deck that URL points at can sync (`ew-042` → that
design's page 42, from whatever `--prefix` it was imported under). A slide
from a different deck just doesn't move this window — the exported image
still shows in the chat preview and `/display` either way.

### Narration scripts

Write a script per slide and the assistant delivers that instead of
improvising. Put them in one plain-text file with the page number above each:

```
หน้า 1
สวัสดีค่ะ ยินดีต้อนรับสู่โครงการ Embassy World
โครงการที่ออกแบบมาเพื่อการใช้ชีวิตที่ดีกว่า

หน้า 2
โครงการของเราได้รับรางวัลด้านการออกแบบมาแล้วหลายรางวัลค่ะ
```

```powershell
python scripts/add_narration.py narration.txt
```

`หน้า 2`, `สไลด์ 2`, `## 2`, `2.`, `Slide 2:` and `[2]` are all accepted, so
you can paste from Word, Docs or Notion without reformatting. Numbers are
deck pages (`--prefix` if your deck wasn't imported as `ew`). Existing
scripts aren't overwritten unless you pass `--replace`, and page numbers
that don't exist are reported rather than silently dropped.

The model is told to deliver the script naturally rather than read it
aloud, to translate it if the guest is speaking another language, and —
importantly — **not to add any number or fact that isn't in it**. Approved
sales copy stays approved.

Slides without a script fall back to the assistant describing them from the
title and summary.

### How slide search works

Hybrid: **BM25 + embeddings, fused with Reciprocal Rank Fusion.**

Thai is written without spaces, so the lexical half tokenises with pythainlp's
`newmm` segmenter (taught this deck's vocabulary) rather than splitting on
whitespace. An earlier version matched on character n-grams instead, and the
failure was specific enough to be worth keeping in mind: `ราคา` matched
`อาคาร`, because they share the run `าคา`. Overlapping letters are not
overlapping meaning.

The semantic half embeds all 144 slides once with `gemini-embedding-001`
(768-dim, L2-normalised) and caches them to `data/slides/embeddings.npz`. This
is what lets a question find a slide sharing none of its words — สระว่ายน้ำ
finding "SKY POOL", or a Chinese guest's 游泳池 finding anything at all. It
degrades rather than fails: with no cache the search is keyword-only and
cross-language matching stops working, which is announced at startup instead
of being left to be discovered.

RRF combines the two rankings without needing their scores to be comparable,
which they aren't — BM25 is unbounded, cosine is [-1, 1].

Slides tagged `other-project` are scored down so a competitor's pool doesn't
answer "show me the pool". Queries below `SEARCH_MIN_SIMILARITY` return
"no match" rather than something unrelated: a wrong floor plan on a large
screen is worse than a blank one. `SEARCH_SHOW_SIMILARITY` is the higher bar a
match must clear before it may take over the screen.

`python scripts/eval_search.py` measures both against
`data/eval_questions.txt` — 126 questions in eight languages, each one taken
from something the deck actually contains, plus 36 that must find *nothing*.

**Measured on this deck, no threshold separates them.** Real questions score
as low as 0.622; off-topic ones reach 0.700. The pair that rejects every bad
question also throws away 47 of the 90 good ones, and only manages it by
switching one of the two signals off entirely. The eval says so in those
words rather than printing a confident number.

So the thresholds shipped here are a chosen trade-off, not a solution: they
favour refusing over guessing, and they do reject a handful of real
cross-language questions. **The fix is content, not arithmetic.** All 59 deck
slides currently carry fewer than three keywords and average 127 characters of
searchable text; that is what makes the distributions overlap. Adding the
words guests actually use will separate them.

The question file is deliberately outside the script: the sales team can add
what they get asked without touching Python, and a threshold calibrated on one
list and asserted against another is not calibrated at all — which happened
here, twice.

### Adding a tool

One decorator in a module under `app/tools/`, then list the module in
`_TOOL_MODULES` in `app/tools/__init__.py`. Both providers pick it up — the
registry emits Gemini `FunctionDeclaration`s and OpenAI tool specs from the
same definition.

```python
@tool(
    name="print_brochure",
    description="พิมพ์โบรชัวร์โครงการให้ลูกค้า",
    parameters={"type": "object", "properties": {
        "copies": {"type": "integer", "minimum": 1, "maximum": 5}}},
)
def print_brochure(copies: int = 1) -> dict:
    return {"ok": True, "copies": copies}
```

Two rules the built-ins follow:

- **Return facts, not sentences.** The model speaks the reply itself. A
  handler returning `"พิมพ์ให้แล้วค่ะ"` locks the answer to Thai; returning
  `{"ok": True, "copies": 2}` lets it answer a Chinese-speaking guest in
  Chinese. (This is the one thing that changed when porting from `emma`,
  where handlers returned finished sentences for a TTS stage.)
- **Never raise.** `dispatch()` turns exceptions into
  `{"ok": false, "error": ...}` so a broken tool doesn't leave the guest in
  silence.

For something slow — walking the robot to a viewing room — pass
`long_running=True`. On Gemini that marks the function `NON_BLOCKING`, so the
conversation keeps going instead of the guest standing in silence for 30
seconds.

### Hardware honesty

IR is one-way: nothing reports back whether the light changed. Every result
carries `hardware`:

| Value | Meaning |
| --- | --- |
| `ok` | code sent to the hub |
| `mock` | no hub configured — state changed, room didn't |
| `failed` | hub unreachable or the send was dropped |

The prompt tells the model to relay that honestly rather than claim success,
and the transcript panel prints a line for every tool result. Claiming "ปิดไฟ
ให้แล้วค่ะ" while the room stays lit is the failure worth designing against.

`data/ir_codes.json` is copied from `emma`, so the codes are already learned.
`data/broadlink_device.json` holds your hub's MAC/IP and is gitignored. With
neither present everything runs in mock mode, which is fine for developing
the conversation.

Set `TOOLS_ENABLED=false` to turn the whole thing off, or
`TOOL_GROUPS=smarthome` to restrict which groups load.

## Who the assistant thinks it is

The persona is built in `app/prompts.py` from two settings:

```
PROJECT_NAME=Embassy World      # said out loud — spell it as it should be spoken
ROBOT_NAME=เอ็มม่า               # optional; blank gives a generic introduction
```

The prompt states the project name twice and explicitly forbids renaming,
abbreviating, translating or guessing it. That rule exists because the model
was answering as "Ambassador World": the name appeared once, so when the
audio was unclear it treated it as something to guess at rather than a fixed
fact. If it still drifts, add the name to `data/condo_facts.json` as well.

It's also told to refuse unrelated work. Without that it drifted into
offering English lessons and translation, which is what a general assistant
does when nothing pins it to a job.

## What the assistant actually knows

Three separate sources, and the difference matters:

| Source | Reaches the model | Use for |
| --- | --- | --- |
| `data/condo_facts.json` | **Always** — in every turn's instructions | Prices, promotions, unit sizes. Authored, with `approved_by` and `effective_from`. Blank fields render as an explicit "no data" line rather than being dropped: a *missing* line reads to the model like a fact nobody mentioned. |
| Slide summaries (144 of them) | **Only when it looks them up** via `search_condo_info` or `show_slide` | Facilities, floors, what the project contains |
| Narration scripts | With the slide being presented | Approved wording for a specific slide |

`search_condo_info` is what lets it answer "มีฟิตเนสไหม" without putting a
picture on screen — before it existed, the whole slide library was invisible
unless the assistant chose to display something.

The summaries are deliberately **not** pasted into the system instructions.
The Live API re-processes and re-bills those every single turn, so 144
summaries would slow down and charge for every reply, including ones that
never mention the project.

**Prices and promotions never come from slides.** Slide text is descriptive,
so it's returned with a note telling the model not to quote it as pricing,
and `search_condo_info` refuses commercial questions **before** searching, in
every language it has been given words for. Those answers come from
`data/condo_facts.json` — see below.

That guard matches Thai terms as *words*, not substrings. Thai is written
without spaces, so `term in text` finds a word inside an unrelated one
constantly: `งบ` (budget) sits inside `ยังไงบ้าง` and `ผ่อน` (instalment)
inside `ผ่อนคลาย` (to relax), so "ที่ออกกำลังกายเป็นยังไงบ้าง" and "ซาวน่าช่วย
ผ่อนคลายไหม" were both being answered with "I have no pricing information".
Same failure as `ราคา` matching inside `อาคาร`, which is why the whole
retrieval layer was rewritten — it had quietly reappeared one function away,
where a wrong refusal costs more than a wrong picture.

## The Canva window is the deck

The presentation the sales team maintains lives in Canva. `data/slides/` is
an *export* of it, and the two drifted: 59 exported frames against a shorter
live deck, several of the frames caught mid-transition between two pages.

`canva_display` used to work out the Canva page from our own filename —
`ew-047` meant page 47. That is arithmetic on our side of the fence, not a
measurement of the other document, and it failed in two visible ways: from
the first divergence onward the window showed a different room from the one
being narrated, and the ids past the end of the deck asked for pages that do
not exist, which is why a guest saw a blank gradient with nothing on it.

So the mapping is measured, and Canva decides the running order:

```
python scripts/canva_pages.py --keep-shots data/canva-shots   # look first
python scripts/canva_pages.py --write                          # then commit to it
```

It opens the real deck, walks every page, screenshots each one and matches
it against the exported images, then writes `data/slides/canva_pages.json`.
`--keep-shots` saves each page beside its matched image so the mapping can
be checked by eye — the similarity scores can say two pictures are alike,
they cannot say the matching is *right*, and fifty side-by-side pictures can.

After that the deck is Canva's, however many pages it has. Frames that
aren't pages of the live deck are not presented at all, and they never move
the window; they remain searchable and can still be put on our own screen by
name. **A slide with no known Canva page leaves the window where it is** — a
mirror that lags is a nuisance, a mirror showing the wrong room while the
robot describes this one is a lie told to a customer.

Re-run it whenever the deck is edited in Canva. Nothing detects that
automatically yet.

### When the deck has actually changed

Measuring it on 2026-08-12 found the design had been edited down from 59
pages to 50, and the offsets say where: they hold at 0 through page 20, step
to +5 at page 27, +8 at page 38 and +9 at page 44. Three sections were
condensed — the entrance floor and Biogenesis, the underground facilities,
and the third floor — and 18 live pages had no picture and no script on this
side at all.

A mapping table can only point at pictures that exist. When the pages
themselves are new, re-export from Canva and rebuild:

```
python scripts/import_canva_export.py ~/Downloads/EmbassyWorld
python scripts/import_canva_export.py ~/Downloads/EmbassyWorld --apply
```

It matches each exported page against the old images and **carries over the
approved title, summary, keywords and narration script for every page that
didn't change** — without that, re-exporting silently discards every script
ever written. Pages that are genuinely new get a blank entry and are listed
at the end together with the old slides that used to sit in that stretch of
the deck, so their wording can be reused by someone entitled to decide what
the project claims. It writes no content of its own.

Afterwards the images *are* the deck: page number and slide id agree by
construction and `canva_pages.json` is an identity, so there is nothing left
to drift. `--apply` backs up `data/slides/` first.

### Why the deck gets walked at startup

Canva's viewer fetches a page when you reach it. Open the deck and jump
straight to page 35 and you do not get page 35 — you get the page template,
a pale empty gradient with nothing on it, while the artwork is still being
requested. The viewer admits this: the progress bar under the page counter
lights up only as far as you have actually walked.

That is the blank screen a guest saw, and no amount of waiting on our side
fixes it, because nothing had asked for the page. A tour that answers
questions can't promise to only ever move one page at a time either — one
"ขอดูฟิตเนสหน่อย" is a jump of twenty pages.

So `warm_deck` walks the whole deck once when the window opens, then returns
to page 1. About half a minute, before anybody is standing there, after
which a jump lands on a picture. `CANVA_WARM_DECK=false` turns it off.

It needs a measured total to know how far to walk, so it does nothing until
`canva_pages.py` has been run — pressing ArrowRight a guessed number of
times into a deck of unknown length is how the first version of all this
went wrong.

## Before using this with real customers

**Four fields in `data/condo_facts.json` are still blank** — starting price,
unit types and sizes, promotions, and the sales office's opening hours. They
are blank on purpose. The project owner's instruction was *"it isn't in the
presentation, I'm not comfortable making it up"*, and the assistant is
instructed to say it has no data and refer the guest to sales rather than
invent a number. Fill them in, set `approved_by`, and verify the behaviour
yourself before a live demo.

**All 59 narration scripts are drafts.** `script_approved` is false for every
one; they were written by a model looking at the slide images and nobody has
checked them. `python scripts/approve_narration.py --all --by "name"` marks
them, and reading them first is the point of the flag existing.

**The documents in `data/documents/` are placeholders** with a large "ไฟล์
ทดสอบ" watermark, so that a test page handed to a customer is obvious. Replace
them with the real files, keeping the filenames in `catalogue.json`.

**Guest transcripts are deleted after 30 days** (`TURN_LOG_KEEP_DAYS`). They
record what members of the public said without being asked, and the assistant
is told to read phone numbers back to confirm them, so numbers end up in there.
The analysis value does not need the raw text: `python scripts/analyze_log.py
--days 30 --save` writes counts to `data/log-summaries/`, which outlive the
deletion and contain nothing anybody said.

**Read the logs before touching the slide keywords.**

```
python scripts/what_guests_ask.py --days 7
```

Three questions, in the guest's own words: what was asked and not found,
what was asked about pricing, and which slides anyone ever requests. The
first list is the input to keyword work — the words worth adding are the
ones real guests missed with, not ones invented at a desk, and they exist
only until the log is deleted.

It repaid itself immediately. The first run said `ไฟ` 27x, `เปิด` 19x,
`ปิด` 15x — switching the lights is by a wide margin the commonest thing
anybody says to this robot — which exposed a hole in a guard written an hour
earlier. See below.

## Nobody said that

Emma's own voice returns through the speakers, the VAD reads it as a guest
starting to talk, and whatever it transcribes arrives as a request. A
recorded run:

```
ผู้ช่วย: ...Embassy World พร้อมเป็นส่วนหนึ่งของความฝันนั้นค่ะ
ลูกค้า: bit like
ผู้ช่วย: ได้ค่ะ ปิดสไลด์เรียบร้อยแล้วค่ะ
```

Nobody said "bit like". The same run had ten sentences chopped mid-word.
`HALF_DUPLEX=true` cures the cause and costs the ability to talk over the
robot; the gallery has not decided to give that up yet.

So there is a second line: **an action needs a word that asked for it.**
`close_presentation` consults `app/heard.py` and, when nothing in what was
heard asks for the screen to stop, asks the guest instead of acting.
Answering a question wrongly is a conversation; closing the deck wrongly
ends the demo.

Thai is matched as *words*, never substrings — `ปิด` sits inside `เปิด`,
which is the opposite instruction. And "turn the lights off" is not "close
the presentation": `ปิดไฟ` survives only because the tokenizer keeps it as
one word, while `ปิดแอร์` splits into `ปิด` + `แอร์` and would have counted.
Depending on which compounds a dictionary happens to contain is not a
design, so room words are checked separately.

## Costs

**The free tier's price is the content.** Google's pricing page lists, for
every model, `Used to improve our products: Free = Yes / Paid = No`. It is a
property of the account tier, not of the model, so switching models does not
change it — only enabling billing does. For a sales gallery recording members
of the public, that is worth deciding deliberately rather than by default.

Paid is not expensive for this shape of use: `gemini-3.1-flash-live-preview`
is $0.005/minute of audio in and $0.018/minute out, so a ten-minute
conversation costs a few baht.

Gemini Live has a free tier, which is why it's the default — good for
development and demos. Check the current
[Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) and
[rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) before
relying on it in production; free-tier quotas are per-minute and per-day, and
a robot left listening all day will hit them.

OpenAI Realtime is paid per minute of audio in *and* out, and is
substantially more expensive than text models. Set a spend limit on your
project before deploying, and check
[current pricing](https://openai.com/api/pricing) — don't assume.

Either way: end the session when nobody is talking to it rather than idling
connected. For a permanently-on robot, a wake-word gate that only opens a
session after "เฮ้ เอ็มม่า" is the usual answer. Not implemented here.

## If replies feel slow

Perceived delay is the sum of several stages, and only some are yours to
control. In rough order of how much they cost:

| Cause | Setting | Notes |
| --- | --- | --- |
| Model thinking before speaking | `GEMINI_THINKING_BUDGET=0` | **Usually the biggest win.** Gemini 2.5 native audio enables dynamic thinking by default; a receptionist reading a fact sheet doesn't need it. |
| Waiting to be sure you stopped | `VAD_SILENCE_MS=500` | Dead air after your last word. Below ~500 ms it starts chopping sentences at natural pauses, which hurts accuracy. |
| Mic buffering | 20 ms chunks (fixed) | Google's guidance is 20–40 ms and warns against buffering more. |
| System prompt length | `app/prompts.py` | Re-processed **and re-billed on every turn** — keep it tight. There's a test guarding the size. |
| Network round trip | — | Thailand → Google. Not tunable; expect a floor of a few hundred ms. |

`gemini-3.1-flash-live-preview` defaults to minimal thinking and is built for
low latency, so it's worth trying — but it drops affective dialogue and
proactive audio, and this project uses `thinking_level` instead of a budget
for `gemini-3.x` models automatically.

Note that Gemini's *first* reply in a session is usually the slowest — the
session is still warming up. Judge latency from the second turn onward.

## Tuning turn-taking

`VAD_SILENCE_MS` (default 500) is how long a pause must last before the
system decides the guest finished. Google's docs recommend **500–800 ms**:
lower fragments one sentence into several chunks and measurably hurts
transcription quality; higher just feels sluggish.

`VAD_PREFIX_PADDING_MS` (default 300) is how much audio *before* detected
speech gets included, so the first syllable isn't clipped.

OpenAI additionally supports `OPENAI_TURN_DETECTION=semantic_vad`, which
decides the guest is done based on whether the sentence *sounds* complete
rather than on silence alone. `OPENAI_VAD_EAGERNESS` trades responsiveness
(`high`) against patience (`low` — better in a noisy gallery).

### If every reply gets tagged "interrupted"

That means the microphone is picking up the robot's **own voice** from the
speakers, and the turn detector is reading it as the guest cutting in. It's
the normal failure mode for a laptop with no headphones — browser echo
cancellation doesn't reliably suppress audio played through the Web Audio
API, which is how reply audio is scheduled here.

In order of preference:

1. **Use headphones.** Instantly removes the feedback path. Best for
   development.
2. `HALF_DUPLEX=true` stops sending mic audio entirely while the assistant
   speaks. Reliable cure, but you lose the ability to talk over it — the
   call screen shows *half-duplex (no barge-in)* when it's on.

**Do not reach for `VAD_START_SENSITIVITY=LOW` here.** The names are
counter-intuitive: per the [Live API
reference](https://ai.google.dev/api/live#StartSensitivity),
`START_SENSITIVITY_LOW` detects the start of speech *less often* — so instead
of ignoring the echo, it ignores **you**, and the robot sits there silently.
Likewise `END_SENSITIVITY_LOW` ends turns less often, which only adds
latency. `HIGH` is the API default for both and what this project ships.

On the actual robot this mostly goes away: the Astronaut's microphone array
does echo cancellation and noise suppression in hardware, which is exactly
the problem a laptop doesn't solve.

### If it never responds at all

Check, in order:

1. The uvicorn terminal — provider errors are logged there.
2. `VAD_START_SENSITIVITY` is `HIGH` (see above; `LOW` will do exactly this).
3. The orb reaches **LISTENING**. If it stays on *connecting*, the session
   never opened; if it shows *error*, the message is in the transcript panel.
4. You're not muted, and the browser tab actually has microphone permission.

## Tests

```
pytest
```

495 tests, no API key and no network (~90s). The OpenAI path runs against a
local WebSocket server impersonating the Realtime API (exercising the real
`websockets` client, real JSON on the wire, and both relay pumps); the Gemini
path runs against a scripted fake live session.

**Almost every test here is a bug that happened in front of a guest.** They
are named after the symptom rather than the function, and the docstring
usually quotes the log or the transcript that produced them. `CLAUDE.md` is
the companion: what broke, and why the fix is shaped the way it is.

Covered, in rough order of how much they cost to learn:

- **Picture vs voice.** The model generates 4–6× faster than it speaks, so
  anything changing what the guest sees has to check the audio lead. Two code
  paths once drove the Canva window on different clocks.
- **The tour advancing itself.** A nudge that fed itself walked 26 slides in
  silence; a later one arrived as a user turn and truncated the narration on
  21 of 81 slides, one to 16% of its script.
- **Stopping and resuming.** "Can you speak Chinese?" was heard as "stop", and
  stopping discarded the deck, so there was no way back.
- **The Canva window.** Kiosk flags landing on a window nobody looks at,
  closing it and having it reopen to show a black page, long jumps reloading
  the whole deck.
- **Printing.** Path resolution, `PrintTo` vs `Print`, and locating a PDF
  helper without depending on a file association.
- **Content rules.** That a browser cannot override the system instructions,
  and that the prompt forbids inventing prices.
- **The transport itself.** Sample rates per provider (the easiest thing to
  get silently wrong), session caps, voice catalogues, audio as binary rather
  than base64, transcripts both directions, interruption reaching the browser,
  OpenAI's truncate carrying the real played duration, unknown-voice fallback,
  per-provider missing-key messages.

Each fix was verified by reverting it and confirming the test goes red. Worth
doing: several tests passed against reverted code the first time, usually
because they stubbed the wrong layer, or because the CI machine lacked a
package and the code never ran at all.

## Not implemented

This is the voice layer, running on a PC. It is not yet on a robot.

- **Wake word** — the session starts when someone presses the button. This is
  the one that costs money rather than convenience: a session left open all
  day burns quota on an empty room. A wake word that opens a session only when
  somebody is actually there is the fix, and it belongs on the robot, not here.
- **The app on the robot.** The server side is done and tested — four tools,
  a protocol, and a mock that lets the whole thing be exercised without
  hardware (`ROBOT_MOCK_PLACES`). What is missing is the Android app that
  receives `{"type": "robot", ...}` and calls `AoboRobotManager`. The spec it
  needs is `docs/ต่อกับหุ่นยนต์ Astronaut.md`, including four questions for the
  vendor that should be asked before anything is built — the riskiest being
  whether the robot's own built-in voice assistant can be turned off. If it
  can't, it will fight this one for the microphone, and that is an
  architecture problem rather than a code one.
- **Guiding a guest, heard end to end.** `go_to_place` returns immediately and
  reports arrival as a separate turn, which is tested; but with no robot the
  mock always answers "can't go anywhere", so the *successful* half of that
  conversation — "this way" ... "we're here" — has never been listened to.
- **The robot's audio path is unverified.** Nothing here has been tested
  against the robot's microphone array or its echo cancellation, and that —
  not the software — is where this is most likely to disappoint. A robot's own
  speaker feeding its own microphone reads as the guest interrupting, on every
  single sentence. `HALF_DUPLEX=true` exists for exactly this and costs the
  ability to talk over the robot, which is a feature people notice losing.
- **Offline fallback** — both providers need network by design. The realistic
  shape is not a local model but a small local command set for the things that
  must work when the line drops: stop, cancel navigation, fetch a human, show
  a QR code, and say that the connection is down.
- **Robot-to-server authentication** — `/ws` is open to anything that can reach
  the port. Fine on a wired gallery LAN, not fine the moment the robot is on
  Wi-Fi or the server is reachable from outside.
- **A spend alert** — `IDLE_TIMEOUT_S` now ends a session with no guest
  speech, and `what_guests_ask.py` reports minutes per day, but nothing
  watches the number for you. Measured on the gallery's own project the headroom is large (peak
  7K tokens/minute against a 65K limit) because one robot means one
  conversation at a time, so this is about tidiness rather than risk.
- **Keywords on the slides.** The one that would actually improve search. All
  65 deck slides carry fewer than three, and the measured overlap between real
  and off-topic questions is a direct consequence. Left undone deliberately:
  the words that matter are the ones guests really use, which are in the sales
  team's heads and will appear in `data/logs/` once this runs in front of
  people. Inventing them here would be a third layer of guessing.

### On running the model locally

Worth stating plainly, because "the API key is in the code" and "we should run
the model locally" get treated as the same problem. They are not.

The key never reaches the robot. `app/providers/` is a proxy, not a
passthrough: the browser (or an app) speaks this project's own WebSocket
protocol, the system instructions are attached server-side, and the client is
never told which provider is behind it. Keeping `.env` on the server — not in
JavaScript, not in an APK — is the whole of what that requires.

So the key is not a reason to go local. Real reasons would be a rule that
audio may not leave the building, or a connection too unreliable to depend on.

And the hardware argues against it anyway. The Astronaut runs Android 10 on a
Snapdragon QCM686 with 4 GB of RAM (8 GB on the upgraded configuration). That
is comfortable for UI, audio capture, a wake word and robot control, and is
not a machine for running speech recognition, a language model and speech
synthesis at once at a quality anyone would put in front of a customer.
