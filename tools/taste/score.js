// node score.js swiper.js eval_out.json -> like/nope agreement between the stored verdict and ours, via swiper's own applyVerdict
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const body = src.slice(src.indexOf('  function num(x)'), src.indexOf('  // ---------------------------------------------------------------- text filters'));
const cfg = { vision: { minFeminine: 8, rejectBodies: 'plus', minBodyConf: 0.5, nopeDyedHair: true, nopePiercings: true, nopeAlt: true, swimwearAutoLike: true,
  minFace: 6, minQuality: 5, likeFace: 8, sexyAutoLike: 7, bustAutoLike: 7, curvesAutoLike: 7, requireFullBody: true, likeBodies: 'slim, athletic', likeMinQuality: 7 } };
const { applyVerdict } = new Function('cfg', body + 'return { applyVerdict };')(cfg);
const rows = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
let agree = 0, likeRef = 0, likeOurs = 0; const t = {};
const traits = { 'face 8+': (v) => +v.face >= 8, 'face 6+': (v) => +v.face >= 6, 'bust 7+': (v) => +v.bust >= 7, 'butt 7+': (v) => +v.glutes >= 7,
  bikini: (v) => v.swimwear === true, 'in shape': (v) => v.in_shape !== false, woman: (v) => v.is_woman === true, 'dyed hair': (v) => !!v.dyed_hair, 'full body': (v) => !!v.full_body_visible };
for (const r of rows) {
  const a = applyVerdict(r.ref), b = applyVerdict(r.ours);
  if (a.d === b.d) agree++; if (a.d === 'like') likeRef++; if (b.d === 'like') likeOurs++;
  for (const [k, fn] of Object.entries(traits)) { t[k] = t[k] || 0; if (fn(r.ref) === fn(r.ours)) t[k]++; }
  console.log((a.d === b.d ? '  same ' : '  DIFF ') + `ref ${a.d} (${a.why}) | ours ${b.d} (${b.why})`);
}
const pct = (x) => Math.round(x * 100 / rows.length) + '%';
console.log(`\n${rows.length} cards: like/nope agreement ${pct(agree)}  (likes: ref ${likeRef}, ours ${likeOurs})`);
console.log('trait agreement: ' + Object.entries(t).map(([k, v]) => `${k} ${pct(v)}`).join(', '));
