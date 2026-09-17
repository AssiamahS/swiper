# NOTES

- 2026-09-16 v1: Shortcut = Safari share sheet → Run JavaScript on Web Page → XHR+eval of swiper.js (Swiperino's shape). Tinder's CSP is `report-only`, so eval, script tags and fetch() to openrouter.ai all work from page context.
- Vision brain = OpenRouter straight from the page (CORS `*`, key stored in tinder.com localStorage). Cloudflare/Workers were NOT needed. Tinder photo CDN sends `access-control-allow-origin: *` so photos can be fetched, canvas-downscaled and sent as data URLs (provider-side URL fetch of signed CDN links fails).
- Free vision models (2026-09-17): with `reasoning: {effort: low}` + max_tokens 1500, nex-n2.5-pro / ling-3.0-flash-vl / dots-3-note-preview all return JSON in content (~6-15s); at 300 tokens they burn the budget on reasoning and content comes back EMPTY. gemma-4 :free rate-limits upstream most of the time. Optional Gemini provider (free AI Studio key, direct REST) added but untested — no key on the Mac.
- Script host = raw.githubusercontent.com (CORS `*`, 300s cache, no Pages build needed). Loader adds `?t=` to dodge cache.
- Selectors come from a 2026-04 userscript (`sparks-like-default` class) plus older `[aria-label="Like"]`; unverified against a live logged-in session from the Mac. Probe button in the Log tab is the debugging path.
