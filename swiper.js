/* swiper — Tinder auto-swiper engine for tinder.com
 * Loaded into the page by an iOS Shortcut (Safari share sheet -> Run JavaScript on Web Page)
 * or pasted in the console. Settings live in localStorage on tinder.com.
 *
 * Features: human-like auto swipe (speed levels, breaks, sleep, caps, hours window),
 * text filters, vision judge (OpenRouter, free models) for body type / photo quality /
 * swimwear, geo spoof (fixed pin or wander, pushed to Tinder), auto first message on match.
 */
(function () {
  'use strict';
  if (window.__swiper) { window.__swiper.show(); return; }

  var VERSION = '1.1.2';
  var LS_CFG = 'swiper.cfg';
  var LS_STATS = 'swiper.stats';

  // ---------------------------------------------------------------- config
  var DEFAULTS = {
    speed: 3,                 // 1 fastest .. 5 slowest
    likeRatio: 0.7,           // like probability when nothing else decides
    maxPerSession: 120,       // then "go to sleep" for a few hours
    maxPerDay: 300,
    breakEvery: [14, 38],     // swipes between micro-breaks
    breakLen: [20, 95],       // seconds
    sleepLen: [120, 240],     // minutes, after maxPerSession
    hours: { enabled: false, start: 18, end: 23 },
    photosToView: [1, 3],     // photos looked at per card (human-like + feeds vision)
    openProfileChance: 0.12,
    filters: {
      maxDistance: 0,         // 0 = off, in the unit Tinder shows
      minAge: 0, maxAge: 0,
      mustHaveBio: false,
      nopeWords: 'liberal, leftist, feminist, socialist, antifa, blm, communist, progressive',
      likeWords: ''
    },
    vision: {
      enabled: true,
      key: '',
      provider: 'openrouter', // openrouter | gemini
      geminiKey: '',
      geminiModel: 'gemini-3.5-flash-lite, gemini-3.1-flash-lite',   // tried in order; each has its own free daily quota
      models: 'nex-agi/nex-n2.5-pro:free, inclusionai/ling-3.0-flash-vl:free, dots-studio/dots-3-note-preview:free, google/gemma-4-31b-it:free',
      rejectBodies: 'plus',   // comma list: slim, athletic, average, curvy, plus
      minBodyConf: 0.5,
      unsure: 'ratio',        // like | nope | ratio when body not judged confidently
      minQuality: 5,          // 0..10, below = grainy/trash -> nope
      swimwearAutoLike: true,
      curvesAutoLike: 7,      // curves score >= this -> like
      maxPhotos: 6,
      requireFullBody: true,
      minFeminine: 6,
      bustAutoLike: 7,          // like at/above (still needs a full-body photo)
      sexyAutoLike: 7,
      minFace: 6,               // nope below this
      likeFace: 8,              // like at/above this (with a full-body photo)
      nopeDyedHair: true,
      likeBodies: '',           // e.g. 'slim, athletic' -> like when body matches and quality >= likeMinQuality
      likeMinQuality: 7,
      onFail: 'wait'            // wait | nope | ratio when the vision call fails
    },
    geo: {
      enabled: false, lat: 40.758, lng: -73.9855, accuracy: 25,
      wander: 0,              // meters, 0 = fixed pin
      pushToTinder: true
    },
    msg: {
      enabled: false,
      text: 'hey :) how\'s your week going',
      ai: false,
      delay: [15, 60]         // seconds before sending after a match
    }
  };

  function deepMerge(base, over) {
    var out = Array.isArray(base) ? base.slice() : {};
    Object.keys(base).forEach(function (k) { out[k] = base[k]; });
    if (!over || typeof over !== 'object') return out;
    Object.keys(over).forEach(function (k) {
      if (base[k] && typeof base[k] === 'object' && !Array.isArray(base[k])) out[k] = deepMerge(base[k], over[k]);
      else out[k] = over[k];
    });
    return out;
  }
  function loadJSON(k, d) { try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } }
  function saveJSON(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

  var cfg = deepMerge(DEFAULTS, loadJSON(LS_CFG, {}));
  function saveCfg() { saveJSON(LS_CFG, cfg); }
  // migrate stale default model lists from older versions
  if (cfg.vision.models === 'google/gemma-4-31b-it:free, nex-agi/nex-n2.5-pro:free, google/gemma-4-26b-a4b-it:free') { cfg.vision.models = DEFAULTS.vision.models; saveCfg(); }
  if (cfg.vision.geminiModel === 'gemini-2.5-flash-lite' || cfg.vision.geminiModel === 'gemini-3.1-flash-lite') { cfg.vision.geminiModel = DEFAULTS.vision.geminiModel; saveCfg(); }
  if (cfg.vision.onFail === 'nope' || cfg.vision.onFail === 'ratio') { cfg.vision.onFail = 'wait'; saveCfg(); } // never swipe blind when the brain is down

  function today() { return new Date().toISOString().slice(0, 10); }
  var stats = loadJSON(LS_STATS, {});
  if (stats.day !== today()) stats = { day: today(), likes: 0, nopes: 0, matches: 0, msgs: 0, judged: 0 };
  function saveStats() { saveJSON(LS_STATS, stats); }

  // ---------------------------------------------------------------- utils
  var running = false, sessionSwipes = 0, sinceBreak = 0, nextBreakAt = 0, wakeLock = null;
  var logBuf = [];
  function rnd(a, b) { return a + Math.random() * (b - a); }
  function rndInt(a, b) { return Math.floor(rnd(a, b + 1)); }
  function skew(a, b) { return a + (b - a) * Math.pow(Math.random(), 1.6); } // mostly fast, sometimes slow
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function log(msg, cls) {
    var line = new Date().toTimeString().slice(0, 8) + ' ' + msg;
    logBuf.push({ t: line, c: cls || '' }); if (logBuf.length > 200) logBuf.shift();
    try { console.log('[swiper] ' + msg); } catch (e) {}
    renderLog(); setStatus(msg);
  }
  var SPEED = { 1: [700, 1600], 2: [1500, 3200], 3: [2500, 6500], 4: [5000, 12000], 5: [10000, 26000] };
  function swipeDelay() { var r = SPEED[cfg.speed] || SPEED[3]; return skew(r[0], r[1]); }

  function visible(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    return r.width > 40 && r.height > 40 && r.bottom > 0 && r.right > 0 &&
      r.top < window.innerHeight && r.left < window.innerWidth;
  }
  function present(el) { if (!el) return false; var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }
  function textOf(el) { return (el && (el.innerText || el.textContent) || '').trim(); }
  function bgUrl(el) {
    var s = (el.style && el.style.backgroundImage) || getComputedStyle(el).backgroundImage || '';
    var m = s.match(/url\(["']?(.*?)["']?\)/); return m ? m[1] : '';
  }

  // ---------------------------------------------------------------- DOM: buttons, cards, modals
  function findButton(kind) {
    var sel = { like: 'button[class*="sparks-like-default"], button[aria-label="Like"]',
                nope: 'button[class*="sparks-nope-default"], button[aria-label="Nope"]' }[kind];
    var b = document.querySelector(sel);
    if (b && present(b)) return b;
    var want = kind === 'like' ? 'like' : 'nope';
    var spans = Array.prototype.slice.call(document.querySelectorAll('button span'));
    for (var i = 0; i < spans.length; i++) {
      if (textOf(spans[i]).toLowerCase() === want) { var p = spans[i].closest('button'); if (p && present(p)) return p; }
    }
    return null;
  }
  function key(k, code) {
    var ev = { key: k, code: code || k, keyCode: { ArrowLeft: 37, ArrowRight: 39, ArrowUp: 38, ArrowDown: 40, ' ': 32, Enter: 13, Escape: 27 }[k] || 0, bubbles: true, cancelable: true };
    document.dispatchEvent(new KeyboardEvent('keydown', ev));
    document.dispatchEvent(new KeyboardEvent('keyup', ev));
  }
  function swipe(kind, forceKeyboard) {
    var b = forceKeyboard ? null : findButton(kind);
    if (b) { b.click(); return 'button'; }
    key(kind === 'like' ? 'ArrowRight' : 'ArrowLeft'); return 'keyboard';
  }

  function photoEls(root) {
    return Array.prototype.slice.call((root || document).querySelectorAll('div[role="img"][aria-label*="Profile Photo" i], div[role="img"][aria-label*="photo" i], img[alt*="photo" i]'));
  }
  function findCard() {
    // Tinder keeps ~3 cards stacked in .recsCardboard__cards; only the top one is aria-hidden="false"
    var top = Array.prototype.slice.call(document.querySelectorAll('.recsCardboard__cards > [aria-hidden="false"], [class*="recsCardboard__cards"] > [aria-hidden="false"]'))
      .filter(function (c) { return c.querySelector('[itemprop="name"], h1') && photoEls(c).length; })[0];
    if (top) return top;
    var names = Array.prototype.slice.call(document.querySelectorAll('[itemprop="name"]')).filter(function (n) { return visible(n) || present(n); });
    var pick = names.filter(function (n) { return !n.closest('[aria-hidden="true"]'); })[0] || names[0];
    if (pick) {
      var c = pick;
      for (var k = 0; k < 12 && c && c !== document.body; k++) { c = c.parentElement; if (c && photoEls(c).length) return c; }
    }
    var els = photoEls(document).filter(visible);
    if (!els.length) return null;
    var el = els[0], node = el;
    for (var i = 0; i < 12 && node && node !== document.body; i++) {
      node = node.parentElement;
      if (node && node.querySelector('[itemprop="name"], h1')) return node;
    }
    node = el; for (i = 0; i < 6 && node.parentElement && node.parentElement !== document.body; i++) node = node.parentElement;
    return node;
  }
  function photoUrls(card) {
    var seen = {}, out = [];
    photoEls(card).forEach(function (el) {
      var u = el.tagName === 'IMG' ? el.currentSrc || el.src : bgUrl(el);
      if (u && /gotinder|tinder/.test(u) && !seen[u]) { seen[u] = 1; out.push(u); }
    });
    return out;
  }
  function nextPhoto(card) {
    var before = photoUrls(card).length;
    var b = card.querySelector('button[aria-label="Next Photo"], button[aria-label*="next photo" i]');
    if (b) { b.click(); return; }
    key(' ', 'Space');
    // fallback: tap the right third of the visible photo
    var el = photoEls(card).filter(visible)[0];
    if (el && photoUrls(card).length === before) {
      var r = el.getBoundingClientRect();
      var x = r.left + r.width * 0.85, y = r.top + r.height * 0.5;
      ['mousedown', 'mouseup', 'click'].forEach(function (t) {
        el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, clientX: x, clientY: y, view: window }));
      });
    }
  }
  function parseCard(card) {
    var p = { name: '', age: 0, distance: null, bio: '', photos: [] };
    var nm = card.querySelector('[itemprop="name"]'), ag = card.querySelector('[itemprop="age"]');
    if (nm) p.name = textOf(nm);
    if (ag) p.age = parseInt(textOf(ag), 10) || 0;
    var h1 = p.name ? null : card.querySelector('h1');
    if (h1) {
      var sp = h1.querySelectorAll('span');
      if (sp.length >= 1) p.name = textOf(sp[0]);
      for (var i = 0; i < sp.length; i++) { var n = parseInt(textOf(sp[i]), 10); if (n >= 18 && n < 100) p.age = n; }
      if (!p.name || !p.age) { var hm = textOf(h1).match(/^(.*?)\s*(\d{2})\s*$/); if (hm) { p.name = p.name || hm[1].trim(); p.age = p.age || parseInt(hm[2], 10); } else if (!p.name) p.name = textOf(h1).trim(); }
    }
    var txt = textOf(card);
    var d = txt.match(/(\d+)\s*(miles?|mi|km|kilomet\w*)\s*away/i);
    if (d) p.distance = parseInt(d[1], 10);
    p.bio = txt.replace(/\s+/g, ' ').slice(0, 700);
    p.photos = photoUrls(card);
    return p;
  }
  function dialogs() { return Array.prototype.slice.call(document.querySelectorAll('[role="dialog"], [aria-modal="true"]')).filter(visible); }
  function findModal() {
    var ds = dialogs();
    for (var i = 0; i < ds.length; i++) {
      var t = textOf(ds[i]);
      if (/it.s a match/i.test(t)) return { kind: 'match', el: ds[i] };
      if (/out of likes|you.re out of likes|likes reset|get more likes/i.test(t)) return { kind: 'outoflikes', el: ds[i] };
      if (/verify|verification|captcha|suspicious|banned|something went wrong/i.test(t)) return { kind: 'verify', el: ds[i] };
      if (/gold|platinum|plus|boost|super like|upgrade|subscribe|premium/i.test(t)) return { kind: 'paywall', el: ds[i] };
      if (ds[i].closest('#swiper-panel')) continue;
      return { kind: 'other', el: ds[i] };
    }
    return null;
  }
  function closeModal(el) {
    var b = el.querySelector('button[aria-label*="close" i], button[title*="close" i]');
    if (!b) {
      var btns = Array.prototype.slice.call(el.querySelectorAll('button'));
      b = btns.find(function (x) { return /no thanks|not now|maybe later|keep swiping|back to tinder|close|dismiss|skip/i.test(textOf(x)); });
    }
    if (b) { b.click(); return true; }
    key('Escape'); return false;
  }

  // ---------------------------------------------------------------- vision judge
  var PROMPT = 'You are rating dating-app profile photos for a personal swipe filter. Look at ALL photos and return ONLY a JSON object, no prose, no markdown:\n' +
    '{"body":"slim|athletic|average|curvy|plus","body_confidence":0-1,"full_body_visible":true|false,"swimwear":true|false,"curves":0-10,"photo_quality":0-10,"grainy":true|false,"group_photo":true|false,"is_woman":true|false,"feminine":0-10,"face":0-10,"dyed_hair":true|false,"bust":0-10,"sexy_vibe":0-10,"notes":"short"}\n' +
    'bust: how large/prominent her chest is (0-10). sexy_vibe: how provocative, flirty or slutty the vibe is (tongue out, suggestive poses, revealing outfits, lingerie; 0 = wholesome, 10 = very provocative). ' +
    'face: how attractive the face and expression are for a dating profile (10 = objectively beautiful, cute or sexy expression like a sorority girl; 0 = unattractive or making ugly faces). dyed_hair: true if hair is an unnatural color (pink, blue, green, purple, etc). ' +
    'Judge across ALL photos, not just the first. full_body_visible: true only if at least one photo shows her from head to at least mid-thigh. swimwear: true if ANY photo shows a bikini, swimsuit or lingerie. ' +
    'body: overall body size of the main person using the clearest full-body photo (plus = visibly heavy/plus-size). curves: how pronounced hips/glutes/hourglass figure are. ' +
    'photo_quality: 10 = sharp, well lit, high-res; 0 = blurry, grainy, dark, pixelated, heavy filters. grainy = true if most photos are low quality. ' +
    'group_photo = true if you cannot tell which person is the profile owner. is_woman: is the profile owner a woman (false for men, boys, or if you cannot tell). feminine: 0 = reads as a man/boy, 10 = unmistakably a woman.';

  function fetchImageAsDataUrl(url, maxSide) {
    return fetch(url, { mode: 'cors', credentials: 'omit' }).then(function (r) {
      if (!r.ok) throw new Error('img ' + r.status); return r.blob();
    }).then(function (blob) {
      return new Promise(function (res, rej) {
        var img = new Image(); var o = URL.createObjectURL(blob);
        img.onload = function () {
          var s = Math.min(1, maxSide / Math.max(img.width, img.height));
          var c = document.createElement('canvas'); c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
          c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
          URL.revokeObjectURL(o);
          try { res(c.toDataURL('image/jpeg', 0.72)); } catch (e) { rej(e); }
        };
        img.onerror = function () { URL.revokeObjectURL(o); rej(new Error('decode')); };
        img.src = o;
      });
    });
  }
  function llm(messages, opts) {
    opts = opts || {};
    var models = (opts.models || cfg.vision.models).split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    var i = 0;
    function tryNext(lastErr) {
      if (i >= models.length) return Promise.reject(lastErr || new Error('no models'));
      var model = models[i++];
      var ctl = new AbortController(); var to = setTimeout(function () { ctl.abort(); }, opts.timeout || 30000);
      return fetch('https://openrouter.ai/api/v1/chat/completions', {
        method: 'POST', signal: ctl.signal,
        headers: { 'Authorization': 'Bearer ' + cfg.vision.key, 'Content-Type': 'application/json',
                   'HTTP-Referer': 'https://assiamahs.github.io/swiper', 'X-Title': 'swiper' },
        body: JSON.stringify({ model: model, temperature: 0, max_tokens: opts.maxTokens || 1500, reasoning: { effort: 'low' }, messages: messages })
      }).then(function (r) { return r.json(); }).then(function (d) {
        clearTimeout(to);
        if (d.error) throw new Error(model + ': ' + (d.error.message || JSON.stringify(d.error)).slice(0, 120));
        var msg = d.choices && d.choices[0] && d.choices[0].message || {};
        var c = msg.content; if (c && typeof c !== 'string') c = JSON.stringify(c);
        // reasoning models sometimes spend the whole budget thinking; salvage a JSON object from the reasoning text
        if ((!c || !/\{[\s\S]*\}/.test(c)) && msg.reasoning && /\{[\s\S]*\}/.test(msg.reasoning)) c = msg.reasoning;
        if (!c) throw new Error(model + ': empty (' + (d.choices && d.choices[0] && d.choices[0].finish_reason) + ')');
        return { model: model, text: c };
      }).catch(function (e) { clearTimeout(to); log('llm ' + e.message, 'warn'); return tryNext(e); });
    }
    return tryNext();
  }
  function gemini(promptText, dataUrls, opts) {
    opts = opts || {};
    var parts = [{ text: promptText }];
    dataUrls.forEach(function (u) {
      var m = /^data:([^;]+);base64,(.*)$/.exec(u);
      if (m) parts.push({ inline_data: { mime_type: m[1], data: m[2] } });
    });
    if (parts.length < 2) return Promise.reject(new Error('no photos could be fetched'));
    var models = (cfg.vision.geminiModel || 'gemini-3.5-flash-lite').split(',').map(function (m) { return m.trim(); }).filter(Boolean);
    var i = 0;
    function tryNext(lastErr) {
      if (i >= models.length) return Promise.reject(lastErr || new Error('gemini: no models'));
      var model = models[i++];
      var ctl = new AbortController(); var to = setTimeout(function () { ctl.abort(); }, opts.timeout || 30000);
      return fetch('https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent?key=' + encodeURIComponent(cfg.vision.geminiKey), {
        method: 'POST', signal: ctl.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ contents: [{ parts: parts }], generationConfig: { temperature: 0, maxOutputTokens: opts.maxTokens || 800, responseMimeType: 'application/json' } })
      }).then(function (r) { return r.json(); }).then(function (d) {
        clearTimeout(to);
        if (d.error) throw new Error(model + ': ' + (d.error.message || '').slice(0, 90));
        var c = d.candidates && d.candidates[0] && d.candidates[0].content && d.candidates[0].content.parts && d.candidates[0].content.parts.map(function (p) { return p.text || ''; }).join('');
        if (!c) throw new Error(model + ': empty');
        return { model: model, text: c };
      }).catch(function (e) { clearTimeout(to); log('gemini ' + e.message, 'warn'); return tryNext(e); });
    }
    return tryNext();
  }
  function firstJson(text) {
    try { var w = JSON.parse(text); if (Array.isArray(w)) w = w[0]; if (w && typeof w === 'object') return w; } catch (e) {}
    var i = text.indexOf('{'); if (i < 0) throw new Error('no json');
    var depth = 0, inStr = false;
    for (var j = i; j < text.length; j++) {
      var ch = text[j];
      if (inStr) { if (ch === '\\') j++; else if (ch === '"') inStr = false; continue; }
      if (ch === '"') inStr = true; else if (ch === '{') depth++; else if (ch === '}') { depth--; if (!depth) return JSON.parse(text.slice(i, j + 1)); }
    }
    throw new Error('unbalanced json');
  }
  function judge(profile) {
    var urls = profile.photos.slice(0, cfg.vision.maxPhotos);
    if (!urls.length) return Promise.reject(new Error('no photos'));
    return Promise.all(urls.map(function (u) {
      return fetchImageAsDataUrl(u, 800).catch(function () { return u; }); // fall back to raw URL
    })).then(function (imgs) {
      var text = PROMPT + (profile.bio ? '\nProfile text: ' + profile.bio.slice(0, 300) : '');
      var viaGemini = function () { return gemini(text, imgs); };
      var viaOpenRouter = function () {
        var content = [{ type: 'text', text: text }];
        imgs.forEach(function (u) { content.push({ type: 'image_url', image_url: { url: u } }); });
        return llm([{ role: 'user', content: content }]);
      };
      // preferred provider first, the other one as a fallback when it has a key
      var order = cfg.vision.provider === 'gemini' ? [[viaGemini, !!cfg.vision.geminiKey], [viaOpenRouter, !!cfg.vision.key]]
                                                  : [[viaOpenRouter, !!cfg.vision.key], [viaGemini, !!cfg.vision.geminiKey]];
      var chain = Promise.reject(new Error('no vision key'));
      order.forEach(function (o) { if (o[1]) chain = chain.catch(function (e) { if (e && e.message !== 'no vision key') log('vision ' + e.message + ', trying next provider', 'warn'); return o[0](); }); });
      return chain;
    }).then(function (r) {
      var v = firstJson(r.text); v._model = r.model;
      if (!Number(v.face) && !Number(v.feminine) && !Number(v.photo_quality) && !Number(v.curves)) throw new Error('empty verdict from ' + r.model);
      return v;
    });
  }
  function visionReady() { return !!(cfg.vision.geminiKey || cfg.vision.key); }
  function applyVerdict(v) {
    var V = cfg.vision;
    var reject = V.rejectBodies.split(',').map(function (s) { return s.trim().toLowerCase(); }).filter(Boolean);
    var q = Number(v.photo_quality); var conf = Number(v.body_confidence);
    var fem = Number(v.feminine);
    if (v.is_woman === false || (!isNaN(fem) && fem < (V.minFeminine || 6))) return { d: 'nope', why: 'not a woman (feminine ' + v.feminine + ')' };
    if (v.grainy === true || (!isNaN(q) && q < V.minQuality)) return { d: 'nope', why: 'grainy/quality ' + q };
    if (reject.indexOf(String(v.body).toLowerCase()) >= 0 && (isNaN(conf) || conf >= V.minBodyConf)) return { d: 'nope', why: 'body ' + v.body + ' (' + conf + ')' };
    var face = Number(v.face);
    if (!isNaN(face) && face < (V.minFace || 0)) return { d: 'nope', why: 'face ' + face };
    if (V.nopeDyedHair && v.dyed_hair === true) return { d: 'nope', why: 'dyed hair' };
    if (V.requireFullBody && v.full_body_visible !== true) return { d: 'nope', why: 'no full-body photo' };
    if (!isNaN(face) && V.likeFace && face >= V.likeFace) return { d: 'like', why: 'face ' + face };
    if (Number(v.sexy_vibe) >= (V.sexyAutoLike || 11)) return { d: 'like', why: 'sexy vibe ' + v.sexy_vibe };
    if (Number(v.bust) >= (V.bustAutoLike || 11)) return { d: 'like', why: 'bust ' + v.bust };
    if (V.swimwearAutoLike && v.swimwear === true) return { d: 'like', why: 'swimwear' };
    if (Number(v.curves) >= V.curvesAutoLike) return { d: 'like', why: 'curves ' + v.curves };
    var likeB = (V.likeBodies || '').split(',').map(function (s) { return s.trim().toLowerCase(); }).filter(Boolean);
    if (likeB.indexOf(String(v.body).toLowerCase()) >= 0 && (isNaN(conf) || conf >= V.minBodyConf) && (isNaN(q) || q >= (V.likeMinQuality || 7))) return { d: 'like', why: 'body ' + v.body + ' q' + q };
    var unsure = !isNaN(conf) && conf < V.minBodyConf;
    if (unsure && V.unsure !== 'ratio') return { d: V.unsure, why: 'unsure -> ' + V.unsure };
    return null; // let ratio decide
  }

  // ---------------------------------------------------------------- text filters
  function textDecision(p) {
    var F = cfg.filters;
    if (F.maxDistance > 0 && p.distance !== null && p.distance > F.maxDistance) return { d: 'nope', why: 'distance ' + p.distance };
    if (F.minAge > 0 && p.age && p.age < F.minAge) return { d: 'nope', why: 'age ' + p.age };
    if (F.maxAge > 0 && p.age && p.age > F.maxAge) return { d: 'nope', why: 'age ' + p.age };
    var bio = (p.bio || '').toLowerCase();
    var words = function (s) { return s.split(',').map(function (x) { return x.trim().toLowerCase(); }).filter(Boolean); };
    var hit = words(F.nopeWords).find(function (w) { return bio.indexOf(w) >= 0; });
    if (hit) return { d: 'nope', why: 'word "' + hit + '"' };
    hit = words(F.likeWords).find(function (w) { return bio.indexOf(w) >= 0; });
    if (hit) return { d: 'like', why: 'word "' + hit + '"' };
    if (F.mustHaveBio && bio.replace(p.name.toLowerCase(), '').length < 40) return { d: 'nope', why: 'no bio' };
    return null;
  }

  // ---------------------------------------------------------------- match + message
  function setNativeValue(el, value) {
    var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
  function aiOpener(profile) {
    var sys = 'Write ONE short, casual, confident first message for a dating app match. Max 110 characters. No emojis unless natural, no hashtags, no em dashes, no quotes. Reference something specific from her profile if there is anything, otherwise keep it light and playful. Output the message only.';
    return llm([{ role: 'system', content: sys }, { role: 'user', content: 'Her name: ' + (profile.name || 'unknown') + '\nProfile text: ' + (profile.bio || '(none)') }],
      { maxTokens: 80, timeout: 20000 }).then(function (r) { return r.text.replace(/^["'\s]+|["'\s]+$/g, '').split('\n')[0]; });
  }
  var lastProfile = null;
  function handleMatch(modal) {
    stats.matches++; saveStats(); renderStats();
    log('MATCH' + (lastProfile && lastProfile.name ? ' with ' + lastProfile.name : ''), 'good');
    if (!cfg.msg.enabled) { closeModal(modal); return sleep(1500); }
    var textP = (cfg.msg.ai && cfg.vision.key) ? aiOpener(lastProfile || {}).catch(function () { return cfg.msg.text; }) : Promise.resolve(cfg.msg.text);
    return sleep(rnd(cfg.msg.delay[0], cfg.msg.delay[1]) * 1000).then(function () { return textP; }).then(function (text) {
      var box = modal.querySelector('textarea, input[type="text"], input:not([type])');
      if (!box || !text) { log('no message box in match modal', 'warn'); closeModal(modal); return; }
      box.focus(); setNativeValue(box, text);
      return sleep(rnd(600, 1500)).then(function () {
        var send = Array.prototype.slice.call(modal.querySelectorAll('button')).find(function (b) { return /^send/i.test(textOf(b)) || b.type === 'submit' || /send/i.test(b.getAttribute('aria-label') || ''); });
        if (send) send.click();
        else box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true }));
        stats.msgs++; saveStats(); renderStats(); log('sent: ' + text, 'good');
        return sleep(1500).then(function () { var m = findModal(); if (m && m.kind === 'match') closeModal(m.el); });
      });
    });
  }

  // ---------------------------------------------------------------- geo spoof (ported from geopin)
  var geoInstalled = false;
  function livePosition() {
    var g = cfg.geo;
    if (g.wander > 0) {
      var t = Date.now() / 1000;
      var dxM = g.wander * 0.7 * Math.sin(t * 0.03), dyM = g.wander * 0.7 * Math.sin(t * 0.037 + 1.3);
      var mLat = 111320, mLng = 111320 * Math.cos(g.lat * Math.PI / 180);
      return { lat: g.lat + dyM / mLat, lng: g.lng + dxM / mLng, speed: 1.2 };
    }
    return { lat: g.lat, lng: g.lng, speed: 0 };
  }
  function installGeo() {
    if (geoInstalled || !navigator.geolocation) return;
    var geo = navigator.geolocation;
    var orig = { get: geo.getCurrentPosition.bind(geo), watch: geo.watchPosition.bind(geo), clear: geo.clearWatch.bind(geo) };
    var watches = {}, wid = 1000000;
    function pos() { var p = livePosition(); return { coords: { latitude: p.lat, longitude: p.lng, accuracy: cfg.geo.accuracy, altitude: null, altitudeAccuracy: null, heading: null, speed: p.speed }, timestamp: Date.now() }; }
    geo.getCurrentPosition = function (ok, err, o) { if (!cfg.geo.enabled) return orig.get(ok, err, o); setTimeout(function () { ok(pos()); }, 30 + Math.random() * 120); };
    geo.watchPosition = function (ok, err, o) {
      if (!cfg.geo.enabled) return orig.watch(ok, err, o);
      var id = ++wid; setTimeout(function () { ok(pos()); }, 30);
      watches[id] = setInterval(function () { if (cfg.geo.enabled) ok(pos()); }, 1000); return id;
    };
    geo.clearWatch = function (id) { if (watches[id]) { clearInterval(watches[id]); delete watches[id]; } else orig.clear(id); };
    geoInstalled = true;
  }
  function tinderToken() {
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (/apitoken/i.test(k)) { var v = localStorage.getItem(k); try { v = JSON.parse(v); } catch (e) {} if (typeof v === 'string' && v.length > 10) return v; }
      }
    } catch (e) {}
    return null;
  }
  function pushLocation() {
    var tok = tinderToken(); var p = livePosition();
    if (!tok) { log('geo: no Tinder API token in localStorage, DOM override only', 'warn'); return Promise.resolve(false); }
    var hdr = { 'X-Auth-Token': tok, 'Content-Type': 'application/json', 'platform': 'web' };
    return fetch('https://api.gotinder.com/v2/meta', { method: 'POST', headers: hdr, body: JSON.stringify({ lat: p.lat, lon: p.lng, force_fetch_resources: true }) })
      .then(function (r) {
        if (r.ok) { log('geo: Tinder location set to ' + p.lat.toFixed(4) + ',' + p.lng.toFixed(4) + ' (v2/meta ' + r.status + ')', 'good'); return true; }
        return fetch('https://api.gotinder.com/user/ping', { method: 'POST', headers: hdr, body: JSON.stringify({ lat: p.lat, lon: p.lng }) })
          .then(function (r2) { log('geo: ping ' + r2.status, r2.ok ? 'good' : 'warn'); return r2.ok; });
      }).catch(function (e) { log('geo push failed: ' + e.message, 'warn'); return false; });
  }

  // ---------------------------------------------------------------- engine
  function withinHours() {
    if (!cfg.hours.enabled) return true;
    var h = new Date().getHours(), s = cfg.hours.start, e = cfg.hours.end;
    return s <= e ? (h >= s && h < e) : (h >= s || h < e);
  }
  function scheduleBreak() { nextBreakAt = sinceBreak + rndInt(cfg.breakEvery[0], cfg.breakEvery[1]); }
  var lastCardKey = '', lastCardAt = 0, lastDecision = null, stuckRetries = 0;
  function loop() {
    if (!running) return;
    step().catch(function (e) { log('error: ' + (e && e.message || e), 'warn'); return sleep(3000); }).then(function () { if (running) loop(); });
  }
  function step() {
    if (!withinHours()) { setStatus('outside hours window, waiting'); return sleep(30000); }
    if (stats.likes + stats.nopes >= cfg.maxPerDay) { log('daily cap reached (' + cfg.maxPerDay + '), stopping'); stop(); return Promise.resolve(); }
    var m = findModal();
    if (m) {
      if (m.kind === 'match') return handleMatch(m.el);
      if (m.kind === 'outoflikes') { log('out of likes, sleeping 3h'); return sleep(3 * 3600 * 1000); }
      if (m.kind === 'verify') { log('verification/blocking dialog, stopping', 'warn'); stop(); return Promise.resolve(); }
      log('closing dialog (' + m.kind + ')'); closeModal(m.el); return sleep(rnd(1500, 3000));
    }
    if (sinceBreak >= nextBreakAt) {
      var b = rnd(cfg.breakLen[0], cfg.breakLen[1]);
      log('micro-break ' + Math.round(b) + 's'); sinceBreak = 0; scheduleBreak(); return sleep(b * 1000);
    }
    if (sessionSwipes >= cfg.maxPerSession) {
      var z = rnd(cfg.sleepLen[0], cfg.sleepLen[1]);
      log('session cap ' + cfg.maxPerSession + ' reached, sleeping ' + Math.round(z) + ' min'); sessionSwipes = 0; return sleep(z * 60000);
    }
    var card = findCard();
    if (!card) { setStatus('no card found, waiting'); return sleep(2500); }
    var p = parseCard(card);
    var ck = p.name + '|' + p.age + '|' + (p.photos[0] || '');
    if (ck === lastCardKey) {
      if (lastDecision && Date.now() - lastCardAt > 8000 && stuckRetries < 2) {
        stuckRetries++; lastCardAt = Date.now();
        var how = swipe(lastDecision, stuckRetries === 1);
        log('card did not advance, retried ' + lastDecision + ' via ' + how, 'warn');
      } else if (stuckRetries >= 2 && Date.now() - lastCardAt > 8000) {
        log('card stuck after retries, stopping. Use Log -> Probe.', 'warn'); stop();
      }
      setStatus('same card still up, waiting'); return sleep(1500);
    }
    lastCardKey = ck; lastCardAt = Date.now(); lastDecision = null; stuckRetries = 0; lastProfile = p;

    var dec = textDecision(p);
    var view = dec ? 0 : rndInt(cfg.photosToView[0], cfg.photosToView[1]);
    var chain = Promise.resolve();
    for (var i = 1; i < view; i++) chain = chain.then(function () { nextPhoto(card); return sleep(rnd(500, 1600)); });
    return chain.then(function () {
      p.photos = photoUrls(card);
      if (!dec && cfg.vision.enabled && visionReady() && p.photos.length) {
        setStatus('judging ' + (p.name || 'card') + '...');
        return judge(p).then(function (v) {
          stats.judged++; var r = applyVerdict(v);
          log((p.name || '?') + (p.age ? ' ' + p.age : '') + ' -> ' + JSON.stringify({ body: v.body, conf: v.body_confidence, q: v.photo_quality, swim: v.swimwear, curves: v.curves, face: v.face, bust: v.bust, sexy: v.sexy_vibe, dyed: v.dyed_hair, fem: v.feminine }) + ' [' + v._model + ']');
          return r;
        }).catch(function (e) {
          if (cfg.vision.onFail === 'nope') { log('vision failed (' + e.message + '), nope', 'warn'); return { d: 'nope', why: 'vision failed' }; }
          if (cfg.vision.onFail === 'ratio') { log('vision failed (' + e.message + '), using ratio', 'warn'); return null; }
          log('vision failed (' + e.message + '), holding 60s (no blind swipes)', 'warn'); return { d: 'wait', why: e.message };
        });
      }
      return dec;
    }).then(function (d) {
      if (d && d.d === 'wait') { lastCardKey = ''; return sleep(60000).then(function () { return null; }); }
      if (!d) d = { d: Math.random() < cfg.likeRatio ? 'like' : 'nope', why: 'ratio' };
      if (Math.random() < cfg.openProfileChance) {
        var ob = card.querySelector('button[aria-label*="open profile" i], button[aria-label*="show more" i]');
        if (ob) { ob.click(); return sleep(rnd(1500, 4000)).then(function () { key('Escape'); return sleep(600); }).then(function () { return d; }); }
      }
      return sleep(swipeDelay()).then(function () { return d; });
    }).then(function (d) {
      if (!d) return;
      var how = swipe(d.d); lastDecision = d.d; lastCardAt = Date.now();
      if (d.d === 'like') stats.likes++; else stats.nopes++;
      sessionSwipes++; sinceBreak++; saveStats(); renderStats();
      log((d.d === 'like' ? 'LIKE ' : 'NOPE ') + (p.name || '?') + ' (' + d.why + ', ' + how + ')', d.d === 'like' ? 'good' : '');
      return sleep(rnd(400, 1200));
    });
  }
  function start() {
    if (running) return;
    if (cfg.vision.enabled && !visionReady()) log('vision on but no key for ' + cfg.vision.provider + ': ratio mode only', 'warn');
    running = true; sessionSwipes = 0; sinceBreak = 0; scheduleBreak(); lastCardKey = '';
    if (cfg.geo.enabled) { installGeo(); if (cfg.geo.pushToTinder) pushLocation(); }
    if (navigator.wakeLock) navigator.wakeLock.request('screen').then(function (w) { wakeLock = w; }).catch(function () {});
    log('started (speed ' + cfg.speed + ', like ratio ' + Math.round(cfg.likeRatio * 100) + '%)'); renderRun(); loop();
  }
  function stop() { running = false; if (wakeLock) { wakeLock.release().catch(function () {}); wakeLock = null; } log('stopped'); renderRun(); }

  function probe() {
    var card = findCard(); var p = card ? parseCard(card) : null;
    var out = ['like btn: ' + (findButton('like') ? 'found' : 'MISSING'), 'nope btn: ' + (findButton('nope') ? 'found' : 'MISSING'),
      'card: ' + (card ? 'found' : 'MISSING'), 'photos loaded: ' + (p ? p.photos.length : 0), 'name/age: ' + (p ? p.name + ' ' + p.age : '-'),
      'distance: ' + (p ? p.distance : '-'), 'token: ' + (tinderToken() ? 'found' : 'MISSING'), 'dialog: ' + (findModal() ? findModal().kind : 'none'),
      'bio: ' + (p ? p.bio.slice(0, 120) : '-')];
    out.forEach(function (l) { log('probe ' + l); });
  }

  // ---------------------------------------------------------------- UI
  var css = '#swiper-panel{position:fixed;z-index:2147483000;right:10px;bottom:90px;width:min(330px,calc(100vw - 20px));font:13px/1.35 -apple-system,system-ui,sans-serif;color:#eee;background:#141416;border:1px solid #333;border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,.5);overflow:hidden}' +
    '#swiper-panel.min{width:auto}#swiper-panel.min .sw-body{display:none}' +
    '#swiper-panel .sw-head{display:flex;align-items:center;gap:8px;padding:9px 11px;background:#1f1f24;cursor:move}' +
    '#swiper-panel .sw-head b{flex:1}#swiper-panel .sw-run{background:#fd5068;color:#fff;border:0;border-radius:8px;padding:6px 12px;font-weight:700}' +
    '#swiper-panel .sw-run.on{background:#2ecc71;color:#111}#swiper-panel .sw-min{background:#333;color:#ccc;border:0;border-radius:6px;padding:4px 8px}' +
    '#swiper-panel .sw-tabs{display:flex;background:#1a1a1e}#swiper-panel .sw-tabs button{flex:1;background:none;border:0;color:#999;padding:7px 0;font-size:12px}' +
    '#swiper-panel .sw-tabs button.on{color:#fff;border-bottom:2px solid #fd5068}' +
    '#swiper-panel .sw-tab{display:none;padding:10px 12px;max-height:55vh;overflow:auto}#swiper-panel .sw-tab.on{display:block}' +
    '#swiper-panel label{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:6px 0;color:#bbb}' +
    '#swiper-panel input[type=text],#swiper-panel input[type=number],#swiper-panel input[type=password],#swiper-panel textarea,#swiper-panel select{width:55%;background:#222;border:1px solid #444;color:#fff;border-radius:6px;padding:5px 7px;font-size:13px}' +
    '#swiper-panel textarea{width:100%;height:56px}#swiper-panel input[type=range]{width:55%}#swiper-panel .wide input[type=text]{width:100%}' +
    '#swiper-panel .sw-stats{display:flex;flex-wrap:wrap;gap:4px 10px;padding:8px 12px;background:#1a1a1e;font-size:12px;color:#aaa}#swiper-panel .sw-stats b{color:#fff}' +
    '#swiper-panel .sw-status{padding:6px 12px;font-size:11px;color:#8a8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-top:1px solid #222}' +
    '#swiper-panel .sw-log{font:11px/1.4 ui-monospace,Menlo,monospace;white-space:pre-wrap;color:#bbb}#swiper-panel .sw-log .good{color:#6f6}#swiper-panel .sw-log .warn{color:#fc6}' +
    '#swiper-panel button.sw-act{background:#333;color:#eee;border:0;border-radius:6px;padding:5px 10px;margin:4px 4px 4px 0}#swiper-panel small{color:#777;display:block;margin:2px 0 6px}';

  function h(tag, attrs, kids) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === 'html') el.innerHTML = attrs[k]; else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), attrs[k]); else el.setAttribute(k, attrs[k]); });
    (kids || []).forEach(function (c) { el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return el;
  }
  function get(path) { return path.split('.').reduce(function (o, k) { return o[k]; }, cfg); }
  function set(path, v) { var ks = path.split('.'), o = cfg; for (var i = 0; i < ks.length - 1; i++) o = o[ks[i]]; o[ks[ks.length - 1]] = v; saveCfg(); }
  function field(labelText, path, type, extra) {
    extra = extra || {};
    var inp;
    if (type === 'check') { inp = h('input', { type: 'checkbox' }); inp.checked = !!get(path); inp.onchange = function () { set(path, inp.checked); if (extra.onchange) extra.onchange(inp.checked); }; }
    else if (type === 'select') { inp = h('select', {}, extra.options.map(function (o) { return h('option', { value: o }, [o]); })); inp.value = get(path); inp.onchange = function () { set(path, inp.value); }; }
    else if (type === 'range') { inp = h('input', { type: 'range', min: extra.min, max: extra.max, step: extra.step || 1 }); inp.value = get(path); var out = h('span', {}, [String(get(path))]); inp.oninput = function () { set(path, Number(inp.value)); out.textContent = inp.value; if (extra.onchange) extra.onchange(Number(inp.value)); }; return h('label', {}, [labelText, h('span', { style: 'display:flex;gap:6px;align-items:center;width:55%' }, [inp, out])]); }
    else if (type === 'pair') { var a = h('input', { type: 'number', style: 'width:26%' }), b = h('input', { type: 'number', style: 'width:26%' }); a.value = get(path)[0]; b.value = get(path)[1]; var upd = function () { set(path, [Number(a.value), Number(b.value)]); }; a.onchange = b.onchange = upd; return h('label', {}, [labelText, h('span', { style: 'display:flex;gap:4px;width:55%;justify-content:flex-end' }, [a, b])]); }
    else if (type === 'textarea') { inp = h('textarea', {}); inp.value = get(path); inp.onchange = function () { set(path, inp.value); }; return h('div', {}, [h('label', {}, [labelText]), inp]); }
    else { inp = h('input', { type: type || 'text', placeholder: extra.placeholder || '' }); inp.value = get(path); inp.onchange = function () { set(path, type === 'number' ? Number(inp.value) : inp.value); if (extra.onchange) extra.onchange(inp.value); }; }
    return h('label', { 'class': extra.wide ? 'wide' : '' }, [labelText, inp]);
  }

  var panel, runBtn, statusEl, statsEl, logEl;
  function build() {
    var style = h('style', { html: css }); document.head.appendChild(style);
    runBtn = h('button', { 'class': 'sw-run', onclick: function () { running ? stop() : start(); } }, ['Start']);
    var minBtn = h('button', { 'class': 'sw-min', onclick: function () { panel.classList.toggle('min'); } }, ['—']);
    var head = h('div', { 'class': 'sw-head' }, [h('b', {}, ['swiper ' + VERSION]), runBtn, minBtn]);
    var tabNames = ['Swipe', 'Filter', 'Vision', 'Geo', 'Msg', 'Log'];
    var tabs = h('div', { 'class': 'sw-tabs' }); var bodies = {};
    tabNames.forEach(function (n, i) {
      var b = h('button', { 'class': i === 0 ? 'on' : '', onclick: function () {
        tabs.querySelectorAll('button').forEach(function (x) { x.classList.remove('on'); }); b.classList.add('on');
        Object.keys(bodies).forEach(function (k) { bodies[k].classList.toggle('on', k === n); });
      } }, [n]); tabs.appendChild(b); bodies[n] = h('div', { 'class': 'sw-tab' + (i === 0 ? ' on' : '') });
    });
    // Swipe
    bodies.Swipe.append(
      field('Speed (1 fast .. 5 slow)', 'speed', 'range', { min: 1, max: 5 }),
      field('Like ratio when unsure', 'likeRatio', 'range', { min: 0, max: 1, step: 0.05 }),
      field('Max per session', 'maxPerSession', 'number'),
      field('Max per day', 'maxPerDay', 'number'),
      field('Break every N swipes (min, max)', 'breakEvery', 'pair'),
      field('Break length sec (min, max)', 'breakLen', 'pair'),
      field('Sleep after session, min (min, max)', 'sleepLen', 'pair'),
      field('Photos to view per card (min, max)', 'photosToView', 'pair'),
      field('Only swipe during hours', 'hours.enabled', 'check'),
      field('From hour (0-23)', 'hours.start', 'number'),
      field('To hour (0-23)', 'hours.end', 'number'),
      h('small', {}, ['Keep Safari on tinder.com/app/recs with the screen on. Re-run the shortcut after any page reload.'])
    );
    // Filter
    bodies.Filter.append(
      field('Max distance (0 = off)', 'filters.maxDistance', 'number'),
      field('Min age (0 = off)', 'filters.minAge', 'number'),
      field('Max age (0 = off)', 'filters.maxAge', 'number'),
      field('Must have a bio', 'filters.mustHaveBio', 'check'),
      field('Nope if bio contains (comma list)', 'filters.nopeWords', 'textarea'),
      field('Like if bio contains (comma list)', 'filters.likeWords', 'textarea')
    );
    // Vision
    bodies.Vision.append(
      field('Vision judge on', 'vision.enabled', 'check'),
      field('Provider', 'vision.provider', 'select', { options: ['openrouter', 'gemini'] }),
      field('OpenRouter key', 'vision.key', 'password', { placeholder: 'sk-or-v1-...' }),
      field('Models (comma, first wins)', 'vision.models', 'textarea'),
      field('Gemini key (aistudio.google.com/apikey)', 'vision.geminiKey', 'password', { placeholder: 'AIza...' }),
      field('Gemini models (comma, first wins)', 'vision.geminiModel', 'text'),
      field('Reject body types', 'vision.rejectBodies', 'text', { placeholder: 'plus  or  plus, curvy' }),
      field('Min body confidence', 'vision.minBodyConf', 'range', { min: 0, max: 1, step: 0.05 }),
      field('When unsure', 'vision.unsure', 'select', { options: ['ratio', 'like', 'nope'] }),
      field('Like only with a full-body photo', 'vision.requireFullBody', 'check'),
      field('Min photo quality (0-10)', 'vision.minQuality', 'range', { min: 0, max: 10 }),
      field('Min feminine score (0-10)', 'vision.minFeminine', 'range', { min: 0, max: 10 }),
      field('Nope if face below (0-10)', 'vision.minFace', 'range', { min: 0, max: 10 }),
      field('Like if face at least (0-10)', 'vision.likeFace', 'range', { min: 0, max: 11 }),
      field('Nope on dyed hair', 'vision.nopeDyedHair', 'check'),
      field('Like body types (comma)', 'vision.likeBodies', 'text', { placeholder: 'slim, athletic' }),
      field('...when photo quality >=', 'vision.likeMinQuality', 'range', { min: 0, max: 10 }),
      field('If vision fails', 'vision.onFail', 'select', { options: ['wait', 'nope', 'ratio'] }),
      field('Swimwear = auto like', 'vision.swimwearAutoLike', 'check'),
      field('Curves score auto like (0-10)', 'vision.curvesAutoLike', 'range', { min: 0, max: 11 }),
      field('Bust score auto like (0-10)', 'vision.bustAutoLike', 'range', { min: 0, max: 11 }),
      field('Sexy vibe auto like (0-10)', 'vision.sexyAutoLike', 'range', { min: 0, max: 11 }),
      field('Photos sent per card', 'vision.maxPhotos', 'number'),
      h('div', {}, [h('button', { 'class': 'sw-act', onclick: function () {
        var c = findCard(); if (!c) return log('no card to test', 'warn'); var p = parseCard(c);
        log('testing vision on ' + (p.name || 'card') + ' (' + p.photos.length + ' photos)');
        judge(p).then(function (v) { log('verdict: ' + JSON.stringify(v)); var r = applyVerdict(v); log('decision: ' + (r ? r.d + ' (' + r.why + ')' : 'ratio')); }).catch(function (e) { log('vision test failed: ' + e.message, 'warn'); });
      } }, ['Test on current card'])]),
      h('small', {}, ['Body type is an estimate from photos, not a scale. "plus" is the visibly heavy bucket; add "curvy" to reject more aggressively. Free OpenRouter models rate-limit sometimes; the list falls through in order. Gemini provider = a free Google AI Studio key, faster and steadier.'])
    );
    // Geo
    bodies.Geo.append(
      field('Spoof location', 'geo.enabled', 'check', { onchange: function (on) { if (on) { installGeo(); if (cfg.geo.pushToTinder) pushLocation(); } } }),
      field('Latitude', 'geo.lat', 'number'),
      field('Longitude', 'geo.lng', 'number'),
      field('Wander radius m (0 = fixed)', 'geo.wander', 'number'),
      field('Accuracy m', 'geo.accuracy', 'number'),
      field('Push to Tinder API', 'geo.pushToTinder', 'check'),
      h('div', {}, [
        h('button', { 'class': 'sw-act', onclick: function () { installGeo(); pushLocation(); } }, ['Apply now']),
        h('button', { 'class': 'sw-act', onclick: function () { navigator.geolocation.getCurrentPosition(function (p) { set('geo.lat', p.coords.latitude); set('geo.lng', p.coords.longitude); log('pin set to ' + p.coords.latitude.toFixed(4) + ',' + p.coords.longitude.toFixed(4)); }, function (e) { log('gps: ' + e.message, 'warn'); }); } }, ['Use real GPS']),
        h('button', { 'class': 'sw-act', onclick: function () { toggleMap(); } }, ['Pick on map'])
      ]),
      h('div', { id: 'sw-map-wrap', style: 'display:none' }, [
        h('div', { style: 'display:flex;gap:6px;margin:6px 0' }, [
          h('input', { id: 'sw-map-q', type: 'text', placeholder: 'city or address', style: 'flex:1;width:auto' }),
          h('button', { 'class': 'sw-act', onclick: function () { geocode(document.getElementById('sw-map-q').value); } }, ['Go'])
        ]),
        h('div', { id: 'sw-map', style: 'height:220px;border-radius:8px;overflow:hidden' }),
        h('small', { id: 'sw-map-hint' }, ['Tap the map to drop the pin. It fills Latitude/Longitude above and pushes to Tinder if Spoof is on.'])
      ]),
      h('small', {}, ['Same override as GeoPin. New cards come from the new spot once the current stack runs out; a full page reload also refreshes it (then re-run the shortcut).'])
    );
    // Msg
    bodies.Msg.append(
      field('Auto message on match', 'msg.enabled', 'check'),
      field('First message', 'msg.text', 'textarea'),
      field('AI opener from her bio (needs key)', 'msg.ai', 'check'),
      field('Send after sec (min, max)', 'msg.delay', 'pair')
    );
    // Log
    logEl = h('div', { 'class': 'sw-log' });
    bodies.Log.append(h('div', {}, [
      h('button', { 'class': 'sw-act', onclick: probe }, ['Probe page']),
      h('button', { 'class': 'sw-act', onclick: function () { logBuf = []; renderLog(); } }, ['Clear']),
      h('button', { 'class': 'sw-act', onclick: function () { stats = { day: today(), likes: 0, nopes: 0, matches: 0, msgs: 0, judged: 0 }; saveStats(); renderStats(); } }, ['Reset stats'])
    ]), logEl);

    statsEl = h('div', { 'class': 'sw-stats' });
    statusEl = h('div', { 'class': 'sw-status' }, ['ready']);
    var body = h('div', { 'class': 'sw-body' }, [tabs].concat(tabNames.map(function (n) { return bodies[n]; })).concat([statsEl, statusEl]));
    panel = h('div', { id: 'swiper-panel' }, [head, body]);
    document.body.appendChild(panel);
    drag(head, panel);
    renderStats(); renderRun();
  }
  // ---- in-panel map (Leaflet + OSM tiles, Nominatim search)
  var map = null, marker = null;
  function loadLeaflet(cb) {
    if (window.L && window.L.map) return cb();
    var css = h('link', { rel: 'stylesheet', href: 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css' }); document.head.appendChild(css);
    var sc = h('script', { src: 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js' }); sc.onload = cb; sc.onerror = function () { log('map library failed to load', 'warn'); }; document.head.appendChild(sc);
  }
  function setPin(lat, lng) {
    set('geo.lat', +lat.toFixed(5)); set('geo.lng', +lng.toFixed(5));
    var ins = panel.querySelectorAll('input[type=number]');
    ins.forEach(function (i) { var l = i.parentElement && i.parentElement.firstChild && i.parentElement.firstChild.textContent; if (l === 'Latitude') i.value = cfg.geo.lat; if (l === 'Longitude') i.value = cfg.geo.lng; });
    if (marker) marker.setLatLng([lat, lng]); else if (map) marker = L.marker([lat, lng]).addTo(map);
    log('pin set to ' + cfg.geo.lat + ',' + cfg.geo.lng);
    if (cfg.geo.enabled) { installGeo(); if (cfg.geo.pushToTinder) pushLocation(); }
  }
  function toggleMap() {
    var w = document.getElementById('sw-map-wrap');
    if (w.style.display !== 'none') { w.style.display = 'none'; return; }
    w.style.display = '';
    loadLeaflet(function () {
      if (!map) {
        map = L.map('sw-map', { zoomControl: true, attributionControl: false }).setView([cfg.geo.lat, cfg.geo.lng], 9);
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
        marker = L.marker([cfg.geo.lat, cfg.geo.lng]).addTo(map);
        map.on('click', function (e) { setPin(e.latlng.lat, e.latlng.lng); });
      }
      setTimeout(function () { map.invalidateSize(); }, 200);
    });
  }
  function geocode(q) {
    if (!q) return;
    fetch('https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(q), { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); }).then(function (d) {
        if (!d.length) return log('no result for "' + q + '"', 'warn');
        var lat = +d[0].lat, lng = +d[0].lon; setPin(lat, lng); if (map) map.setView([lat, lng], 11);
        log('found ' + d[0].display_name.slice(0, 60));
      }).catch(function (e) { log('search failed: ' + e.message, 'warn'); });
  }
  function drag(handle, el) {
    var sx, sy, ox, oy, on = false;
    function down(e) { if (e.target && e.target.closest('button')) return; var p = e.touches ? e.touches[0] : e; on = true; sx = p.clientX; sy = p.clientY; var r = el.getBoundingClientRect(); ox = r.left; oy = r.top; e.preventDefault(); }
    function move(e) { if (!on) return; var p = e.touches ? e.touches[0] : e; el.style.left = Math.max(0, ox + p.clientX - sx) + 'px'; el.style.top = Math.max(0, oy + p.clientY - sy) + 'px'; el.style.right = 'auto'; el.style.bottom = 'auto'; }
    function up() { on = false; }
    handle.addEventListener('mousedown', down); window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
    handle.addEventListener('touchstart', down, { passive: false }); window.addEventListener('touchmove', move, { passive: true }); window.addEventListener('touchend', up);
  }
  function renderStats() { if (statsEl) statsEl.innerHTML = 'today <b>' + stats.likes + '</b> likes <b>' + stats.nopes + '</b> nopes <b>' + stats.matches + '</b> matches <b>' + stats.msgs + '</b> msgs <b>' + stats.judged + '</b> judged'; }
  function renderRun() { if (runBtn) { runBtn.textContent = running ? 'Stop' : 'Start'; runBtn.classList.toggle('on', running); } }
  function setStatus(s) { if (statusEl) statusEl.textContent = s; }
  function renderLog() { if (!logEl) return; logEl.innerHTML = logBuf.slice(-80).reverse().map(function (l) { return '<div class="' + l.c + '">' + l.t.replace(/</g, '&lt;') + '</div>'; }).join(''); }

  build();
  if (cfg.geo.enabled) installGeo();
  log('loaded on ' + location.pathname);
  if (!/\/app\/recs/.test(location.pathname)) log('open tinder.com/app/recs and press Start', 'warn');
  window.__swiper = { show: function () { panel.style.display = ''; panel.classList.remove('min'); }, cfg: cfg, start: start, stop: stop, probe: probe, judge: judge, version: VERSION };
})();
