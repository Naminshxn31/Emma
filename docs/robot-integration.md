# Robot integration notes

Current delivery checklist and verified limitations: [9 September readiness review](robot-arrival-2026-09-09.md). No Android bridge APK/AAR is bundled; software tests do not certify hardware readiness.

Interactive command rehearsal is now available at `http://127.0.0.1:8010`
after running `start-robot-simulator.cmd`. See [the simulator guide](robot-simulator.md)
for source PDF page references, command lifecycle tests, and the remaining
Android/AAR work. This is our simulator, not the vendor's SDK MOCK runtime.

Source documents reviewed:

- `aobo_robot_sdk_v2_thai.pdf` (35 pages)
- `astronaut_robot_manual_th.pdf` (11 pages)
- `c7ca2a1e0b380184867496a81c0d59d1.pdf` (Chinese SDK source, 35 pages)

## Confirmed architecture

The navigation SDK is an Android AAR (`aoborobotsdk-v2.0.aar`) built around
`AoboRobotManager`. It supports `SdkMode.REAL` and `SdkMode.MOCK`, connects to
the navigation controller by IP and TCP port (default `1445`), and must run in
the Android application on the robot. The Python voice server should therefore
continue sending high-level commands over its existing WebSocket; it should
not attempt to load the AAR directly.

The current command contract maps cleanly to the documented SDK:

| Python/WebSocket action | Android SDK responsibility |
|---|---|
| `move_to_point` | resolve a saved POI and start navigation |
| `cancel_navigation` | `manager.cancelNavigation()` |
| `go_home` | `manager.goHome()` with the robot's configured charging base |
| robot status | navigation callbacks + `manager.getBatteryPercentage()` |
| POI refresh | enumerate the map's saved points after connect/map load |

The current server accepts `robot_ready` and `robot_arrived` (with explicit
boolean `ok: true` or `ok: false` and the matching destination). These are the
implemented wire names. Status/battery telemetry, command IDs, ACKs and
heartbeat handling still require a bridge/server protocol extension; do not
send `arrived`, `navigation_failed` or `status` expecting current handlers.
A tool call must not wait for the robot to finish walking.

## Audio hardware facts to verify on arrival

The SDK document describes an 8-channel microphone-array interface with audio
detection, wake-word/recognition hooks, self-test, sound-card checks, and audio
capture. The Astronaut product manual describes a four-microphone assembly with
source localization, noise resistance, interruption, and echo cancellation.
These may describe different configurations. Do not hard-code channel count or
audio device number until `MicArrayManager` self-test is run on the delivered
unit.

Record on the real robot:

1. exact product/firmware/AAR version;
2. microphone channel count and ALSA/Android audio device;
3. whether hardware AEC remains active when raw PCM is captured;
4. sample rate, sample width, channel layout, and beamformed/raw output;
5. end-of-speech and interruption latency at 1, 2, and 4 metres;
6. navigation callback payloads and failure reason values;
7. POI enumeration and home/charging point semantics;
8. battery/charging event frequency and localization-quality fields.

## Acceptance test on hardware

- Run SDK `MOCK` first, then `REAL` on a closed test map.
- Confirm emergency stop/cancel before testing named destinations.
- Test unknown and ambiguous POIs; the server must ask rather than move.
- Disconnect Wi-Fi during navigation and verify state becomes unknown.
- Capture 100+ utterances with the robot speaking to measure AEC/barge-in.
- Tune VAD only after those recordings exist; current Gemini server-side VAD
  remains the baseline.
