# Emma Rive source

`scene.rml` is the editable rig and motion source for the Emma mascot. The
transparent artwork layers in `assets/` are extracted from the approved Emma
atlas and embedded in the runtime asset at `client/assets/emma/emma.riv`.

From the repository root:

```powershell
python .\scripts\extract-emma-rive-assets.py
.\scripts\build-emma-rive.ps1
```

Run the extraction command again only when the approved source atlas changes.
It also removes disconnected pixels from neighbouring atlas cells so the Rive
layers do not contain duplicate shoulders or ring fragments.

The `EmmaVoice` state machine exposes these inputs to the app:

- `mode`: `0` idle, `1` greeting, `2` listening, `3` thinking/loading,
  `4` speaking, `5` happy/grateful, `6` error/frustrated
- `speechLevel`: `0..100`
- `lookX`, `lookY`: `0..100`, with `50` at the centre
- `gesture`: trigger the greeting animation

The page keeps the earlier SVG/PNG puppet as a fallback. It is hidden only
after the Rive runtime loads the artboard and all state machine inputs.
