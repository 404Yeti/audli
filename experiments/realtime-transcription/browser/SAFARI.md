# iPhone Safari compatibility: assessment and test plan

Status: **not yet tested on a device** (WebKit cannot run in this environment and no iPhone is available to the assistant). What is below is what the platform and the API are known to allow, what the page checks, and the pass criteria for a real-device run.

## What the streaming path needs, and the known risks
| Need | Why it matters on iOS Safari | Mitigation in the test page |
|---|---|---|
| Secure context + user gesture | `getUserMedia` and `AudioContext.resume()` only work over HTTPS (localhost counts only on the same device) after a tap | Start button creates the `AudioContext` inside the tap; serve through an HTTPS tunnel |
| `AudioWorklet` | Supported on current iOS (14.5+); older versions fall back poorly | Capability panel records support; the page fails visibly rather than silently |
| Hardware sample rate (44.1 / 48 kHz, not 24 kHz) | The API wants 24 kHz PCM16 | Streaming linear resampler (unit-tested at 44.1 and 48 kHz, chunk-continuous) |
| Mic stays open during the session | Today the app opens a fresh stream per turn; a continuous stream shows the recording indicator and changes the audio session | The page keeps one stream for many turns so we learn whether iOS re-prompts or degrades |
| Playback while the mic is open (Pip speaking) | iOS switches to a play-and-record route: volume drop, earpiece routing or echo | "Beep while mic is open" button; log of `AudioContext.state` (`interrupted`, `suspended`) |
| Interruptions | Calls, Siri, lock screen and tab backgrounding suspend capture and can kill the WebSocket | Page logs `visibilitychange` and context state changes; reconnection is a production-design question, not solved here |
| Browser WebSocket auth | Browsers cannot set headers; the short-lived secret travels in a subprotocol | Verified from a non-browser client (`capabilities.json`); the page tests it in real Safari |
| Raw audio never stored | Privacy rule | The page keeps no audio, only numbers; transcript text is shown, never saved |

## How to run it
1. `OPENAI_API_KEY=... python experiments/realtime-transcription/browser/serve.py` (binds to 127.0.0.1; prints a URL ending in a random `#token`).
2. Put an HTTPS tunnel in front of port 8765 (for example `cloudflared tunnel --url http://127.0.0.1:8765`), open the tunnel URL **with the same `#token`** on the iPhone. Stop the server and tunnel when finished.
3. Tap Start, allow the microphone, say a few English sentences with natural pauses; repeat for 20 consecutive turns; tap "Beep" once mid-session; lock the phone for 10 s and unlock; switch Wi-Fi / cellular once.
4. Download numbers; send the JSON (no audio, no transcripts) back. Repeat in Chrome on desktop as the reference.

## Pass criteria (from the proposal)
* 20 consecutive turns complete on at least 3 runs, no capture failure, no stuck `interrupted` state.
* Commit-to-final latency on the phone within 300 ms of the desktop reference.
* Playback while the mic is open does not make later turns fail; any volume or route change is recorded.
* The socket survives a screen lock of 10 s, or the failure mode is clear enough to design a reconnect.
Any failure is a "no-go for production integration until designed around", not a reason to change the production recorder.
