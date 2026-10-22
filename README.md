# swiper

Tinder auto-liker that runs inside Safari on the iPhone. One Shortcut, no app, no server.

Share sheet on `tinder.com/app/recs` → **Swiper** → a panel appears with Start/Stop and tabs:

| Tab | What |
|---|---|
| Swipe | speed 1–5, like ratio, session/day caps, micro-breaks, sleep, photos viewed per card, hours window |
| Filter | max distance, age range, must-have-bio, nope/like keyword lists |
| Vision | OpenRouter key + model list, reject body buckets, min confidence, min photo quality, swimwear auto-like, curves auto-like |
| Geo | GeoPin-style geolocation override + push to Tinder's location endpoint, fixed pin or wander radius |
| Msg | auto first message on match (fixed text or AI opener from her bio) |
| Log | every decision with reason + model; Probe reports which page elements were found |

## 1.8.0 — what the Tinder teardown changed (2026-10-21)

[TheDonAyo pulled Tinder's on-device model apart](https://x.com/thedonayo/status/2112591782857109809): your feed is
the **average embedding of every photo you liked**, every card logs **photo view count + stare time**
(`RecsStatusDetails`), and you are ranked and upsold by **RSRR** (right-swipe-received ratio, `mn.bin` outputs
`rsrrMale/rsrrFemale/nudity/underage/face/fullBody`). So:

| Change | Why |
|---|---|
| Taste guard: rolling like share capped at 40 % over 40 swipes, only a *strong* like (two reasons or a 9+ face/butt) passes the cap; `unsure` = nope, ratio = 0 | loose likes get averaged into your "type" and you get shown the wrong deck |
| Dwell: after a like the loop lingers 2–4 extra photos at 0.9–2.2 s each, after a nope 0–1 at 0.25–0.6 s | stare time per card is a taste signal, and a flat 1-photo-per-card bot is a fingerprint |
| Match poll: `/v2/matches` every 10 min + on Start, new matches logged as `MATCH (later)` and sent to the live feed | the "It's a Match" modal only fires when she liked first; two weeks of feed showed 74 likes and zero MATCH lines because later matches were never counted |
| `tools/photo_score.py` | scores *your* photos by the leaked RSRR factors (mirror selfie, outdoors, sunglasses, shirtless, face, full body, smile, group) + the public match-rate data, prints an order and a reshoot list; `--tinder` pulls the live profile from the Dia tab |

## How it loads

Same mechanism as Swiperino: the Shortcut receives the Safari page, runs a tiny loader
(`shortcut/loader.js`) with "Run JavaScript on Web Page", which fetches `swiper.js` and evals it
inside tinder.com. The script is served from raw.githubusercontent.com (CORS `*`, ~5 min cache), so
pushing to `main` updates every phone on the next run. Tinder's CSP is report-only.

## Vision judge

Photos are read from the card (`div[role=img][aria-label^="Profile Photo"]` background images),
fetched cross-origin (the CDN sends `access-control-allow-origin: *`), downscaled to 640px in a
canvas and sent as base64 to OpenRouter chat completions with a JSON-only prompt. Free models are
tried in order; the response is `{body, body_confidence, full_body_visible, swimwear, curves,
photo_quality, grainy, group_photo}`.

Decision order: text filters → vision (grainy → nope, rejected body bucket at ≥ confidence → nope,
swimwear → like, curves ≥ threshold → like, unsure → configured action) → like ratio.

## Build the shortcut

```
python3 shortcut/build_shortcut.py            # signs with `shortcuts sign --mode anyone`
python3 shortcut/build_shortcut.py --url https://…/swiper.js
```

## Selectors (Tinder web, 2026)

Like/Nope: `button[class*="sparks-like-default"]` / `sparks-nope-default`, then `[aria-label="Like"|"Nope"]`,
then a `<button>` whose span text is "Like"/"Nope", then ArrowRight/ArrowLeft keydown.
Card = nearest ancestor of the visible profile photo that contains an `h1` (name + age spans).
Match modal = `[role=dialog]` containing "It's a Match". Use **Log → Probe** when Tinder ships a redesign.
