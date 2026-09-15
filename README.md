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
