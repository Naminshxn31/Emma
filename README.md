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
| Model | `gemini-2.5-flash-native-audio-preview-12-2025` | `gpt-realtime-2.1` |
| Voices | 30 | 10 |
| Mic audio | PCM16 **16 kHz** | PCM16 **24 kHz** |
| Reply audio | PCM16 24 kHz | PCM16 24 kHz |
| Session cap | 15 min | 60 min |
| Interruption | server drops unsent audio; client just stops | client must also report how much was heard |
| Extras | affective dialogue, proactive audio | semantic VAD |
| Key | `GEMINI_API_KEY` ([free](https://aistudio.google.com/apikey)) | `OPENAI_API_KEY` |

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
| `app/prompts.py` | System instructions + condo facts (**fill these in**) |
| `app/voices.py` | Voice catalogues per provider |
| `client/index.html` | Voice picker, live call UI, mic capture, playback, barge-in |

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

Built in (`app/tools/smarthome.py`, IR codes ported from `emma`):

| Tool | Does |
| --- | --- |
| `set_lights` | on / off |
| `set_air_conditioner` | on / off, temperature 16–30, fan, mode |
| `get_room_status` | what was last commanded |

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

Thai is written without spaces, so matching is character n-gram overlap
across titles, summaries and keywords in both languages — whole-string
containment failed on near misses ("ทำเลที่ตั้ง" found nothing despite a
"แผนที่ทำเล" slide existing). Slides tagged `other-project` are scored down
so a competitor's pool doesn't answer "show me the pool".

Queries below `MIN_MATCH_SCORE` return "no match" instead of showing
something unrelated — a wrong floor plan on a large screen is worse than a
blank one. That threshold only filters obvious noise; n-grams can't separate
"การเมือง" from "ในเมือง", so staying on topic remains the prompt's job.

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
fact. If it still drifts, put the name in `CONDO_FACTS` as well.

It's also told to refuse unrelated work. Without that it drifted into
offering English lessons and translation, which is what a general assistant
does when nothing pins it to a job.

## What the assistant actually knows

Three separate sources, and the difference matters:

| Source | Reaches the model | Use for |
| --- | --- | --- |
| `CONDO_FACTS` in `app/prompts.py` | **Always** — in every turn's instructions | Prices, promotions, unit sizes. Authored and approved. |
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
and `search_condo_info` returns nothing for "ราคาเท่าไหร่". That has to be
filled into `CONDO_FACTS` — see below.

## Before using this with real customers

`app/prompts.py` ships with **placeholder condo facts**. Fill in
`CONDO_FACTS` with real prices, unit types, facilities, and promotions. Until
you do, the assistant is instructed to say it doesn't have that information
and refer the guest to sales staff rather than inventing numbers — but verify
that behaviour yourself before a live demo.

## Costs

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

29 tests, no API key and no network. The OpenAI path runs against a local
WebSocket server impersonating the Realtime API (exercising the real
`websockets` client, real JSON on the wire, and both relay pumps); the Gemini
path runs against a scripted fake live session.

Covered: sample rates per provider (the easiest thing to get silently wrong),
session caps, both voice catalogues matching the documented IDs, audio
arriving as binary rather than base64, transcripts in both directions,
interruption reaching the browser, OpenAI's truncate carrying the real played
duration, unknown-voice fallback, per-provider missing-key messages, that a
browser cannot override the system instructions, and that the prompt forbids
inventing prices.

## Not implemented

- **Wake word** — the session starts when someone presses the button.
- **Robot hardware** — this is the voice layer. Driving the Astronaut
  robot's arms/navigation would go through its Android SDK
  (`AoboRobotManager`), triggered from function calls.
- **Offline fallback** — both providers need network by design.
