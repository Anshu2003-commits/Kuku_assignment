/* Browser port of promo_finder/parse.py + candidates.py.
   Deterministic half of the pipeline only - the LLM judge needs a key and
   does not run client-side. Verified against the Python on the real pack. */

const RE_EP_NUM    = /^EPISODE NUMBER:\s*(?:Episode\s*)?(\d+)\s*$/i;
const RE_EP_LOG    = /^EPISODE LOGLINE:\s*(.+)$/i;
const RE_SCRIPT_ST = /^---\s*SCRIPT START\s*---\s*$/i;
const RE_SCRIPT_EN = /^---\s*SCRIPT END\s*---\s*$/i;
const RE_SCENE     = /^SCENE\s+(\d+)\s*$/i;
const RE_SCENE_HD  = /^Scene heading:\s*(.+)$/i;
const RE_SCENE_TM  = /^Scene (start|end):\s*(\d+:\d+(?:\.\d+)?)\s*$/i;
const RE_BEAT_HD   = /^\*\*(.+?):\*\*\s*$/;
const RE_BEAT_TM   = /^Start:\s*\((\d+:\d+(?:\.\d+)?)\)\s*\|\s*End:\s*\((\d+:\d+(?:\.\d+)?)\)\s*$/i;

const NON_DIALOGUE = new Set(["CAMERA", "ACTION", "SOUND FX", "SFX", "MUSIC", "VFX", "TEXT"]);
const MODIFIERS    = new Set(["O.S.", "OS", "V.O.", "VO", "CONT'D", "CONTD", "OFF"]);

export const MIN_PROMO_S = 20, MAX_PROMO_S = 60, HOOK_WINDOW_S = 3;

export function parseTimestamp(ts) {
  const p = ts.split(":");
  if (p.length === 2) return parseInt(p[0], 10) * 60 + parseFloat(p[1]);
  if (p.length === 3) return parseInt(p[0], 10) * 3600 + parseInt(p[1], 10) * 60 + parseFloat(p[2]);
  throw new Error("bad timestamp " + ts);
}

export function fmt(sec) {
  const m = Math.floor(sec / 60);
  return String(m).padStart(2, "0") + ":" + (sec - m * 60).toFixed(2).padStart(5, "0");
}

function splitLabel(label) {
  const groups = [...label.matchAll(/\(([^()]*)\)/g)].map(m => m[1]);
  let base = label.replace(/\(([^()]*)\)/g, "").replace(/\s+/g, " ").trim().replace(/^[-\s]+|[-\s]+$/g, "");
  const mods = [], emos = [];
  const norm = new Set([...MODIFIERS].map(m => m.replace(/\s/g, "").toUpperCase()));
  for (const g of groups) {
    const t = g.trim();
    if (!t) continue;
    if (norm.has(t.replace(/\s/g, "").toUpperCase())) mods.push(t);
    else emos.push(...t.split(",").map(x => x.trim()).filter(Boolean));
  }
  const up = base.toUpperCase();
  if (NON_DIALOGUE.has(up)) return { kind: up, speaker: "", emotions: emos, modifiers: mods };
  return { kind: "DIALOGUE", speaker: base, emotions: emos, modifiers: mods };
}

export function parseEpisode(text, fallbackNum = 1) {
  const lines = text.split(/\r?\n/);
  const ep = { number: fallbackNum, logline: "", scenes: [], beats: [], warnings: [] };
  let inScript = false, curScene = 0, heading = "", sceneStart = null;
  let pending = null, body = [];

  const flush = () => {
    if (!pending) return;
    const idx = ep.beats.length;
    ep.beats.push({
      id: `E${String(ep.number).padStart(2, "0")}.B${String(idx).padStart(3, "0")}`,
      index: idx, scene: pending.scene, kind: pending.kind, speaker: pending.speaker,
      emotions: pending.emotions, start_s: pending.start_s, end_s: pending.end_s,
      text: body.map(l => l.trim()).filter(Boolean).join(" ").trim(),
    });
    pending = null; body = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const s = lines[i].trim();
    if (!s) continue;
    if (!inScript) {
      let m;
      if ((m = RE_EP_NUM.exec(s))) ep.number = parseInt(m[1], 10);
      else if ((m = RE_EP_LOG.exec(s))) ep.logline = m[1].trim();
      else if (RE_SCRIPT_ST.test(s)) inScript = true;
      continue;
    }
    if (RE_SCRIPT_EN.test(s)) { flush(); inScript = false; continue; }
    let m;
    if ((m = RE_SCENE.exec(s))) { flush(); curScene = parseInt(m[1], 10); heading = ""; sceneStart = null; continue; }
    if ((m = RE_SCENE_HD.exec(s))) { heading = m[1].trim(); continue; }
    if ((m = RE_SCENE_TM.exec(s))) {
      const t = parseTimestamp(m[2]);
      if (m[1].toLowerCase() === "start") sceneStart = t;
      else ep.scenes.push({ number: curScene, heading, start_s: sceneStart ?? 0, end_s: t });
      continue;
    }
    if ((m = RE_BEAT_HD.exec(s))) {
      flush();
      const { kind, speaker, emotions } = splitLabel(m[1]);
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      const tm = j < lines.length ? RE_BEAT_TM.exec(lines[j].trim()) : null;
      if (!tm) { ep.warnings.push(`beat header ${s} has no Start/End line`); continue; }
      pending = { scene: curScene, kind, speaker, emotions,
                  start_s: parseTimestamp(tm[1]), end_s: parseTimestamp(tm[2]) };
      i = j;
      continue;
    }
    if (RE_BEAT_TM.test(s)) { ep.warnings.push(`orphan timestamp: ${s}`); continue; }
    if (pending) { body.push(s); continue; }
    if (/^actions and dialogue/i.test(s)) continue;
    ep.warnings.push(`unrecognised line: ${s.slice(0, 80)}`);
  }
  flush();
  ep.duration = ep.scenes.length ? Math.max(...ep.scenes.map(s => s.end_s))
                                 : Math.max(0, ...ep.beats.map(b => b.end_s));
  return ep;
}

/* ---- heuristic scoring (mirrors candidates.py SIGNALS) ---------------- */
const SIG = {
  hook_action: { w: 3.0, p: [
    /\bslap(s|ped|ping)?\b/i, /\bstrik(e|es|ing)\b/i, /\bstruck\b/i, /\bpunch(es|ed)?\b/i,
    /\bshove(s|d)?\b/i, /\bgrab(s|bed|bing)?\b/i, /\bcrack(s|ed)?\b/i, /\bwhip\b/i,
    /\bcrash(es|ed|ing)?\b/i, /\bsmash(es|ed|ing)?\b/i, /\bshatter(s|ed|ing)?\b/i,
    /\bcollapse(s|d)?\b/i, /\bfalls? to (her|his) knees\b/i, /\bscream(s|ed|ing)?\b/i,
    /\bgasp(s|ed|ing)?\b/i, /\bshriek\b/i, /\bblood\b/i, /\bblade\b/i, /\bsword\b/i,
    /\bdagger\b/i, /\bgun\b/i, /\bdraws? (a|his|her) (blade|sword|dagger|pistol)\b/i,
    /\bslams?\b/i, /\bkicks? (open|down)\b/i, /\bdrags?\b/i, /\bthrows? (her|him|it)\b/i,
    /\bchok(e|es|ing)\b/i, /\bstabs?\b/i, /\bexplo(de|des|sion)\b/i, /\bgunshot\b/i,
    /\bfires? (a|his|her|the) (gun|pistol|musket|shot)\b/i ] },
  hook_emotion: { w: 2.5, p: [
    /outrag/i, /shock/i, /gloat/i, /furious/i, /fury/i, /venom/i, /horrifi/i, /mock/i,
    /accus/i, /scream/i, /bark/i, /snarl/i, /vicious/i, /cruel/i, /savage/i, /seething/i,
    /enraged/i, /contempt/i, /triumph/i, /malice/i, /menac/i, /threat/i, /desperate/i,
    /betrayed/i, /devastat/i, /piercing/i, /commanding/i ] },
  hook_line: { w: 2.5, p: [
    /\byou knew\b/i, /\bhow long\b/i, /\bwho are you\b/i, /\bwhat are you\b/i,
    /\bshe'?s alive\b/i, /\bhe'?s alive\b/i, /\bi'?m your\b/i, /\byou'?re my\b/i,
    /\bmarry me\b/i, /\byou swore\b/i, /\byou promised\b/i, /\byou lied\b/i,
    /\bi changed my mind\b/i, /\bnot anymore\b/i, /\bget out\b/i, /\bhow dare you\b/i,
    /\bi'?ll kill\b/i, /\bkneel\b/i, /\byou let me\b/i, /\bit was you\b/i, /\bi am the\b/i,
    /\bdo you know who\b/i, /\bmy (wife|husband|son|daughter)\b/i, /\bduke\b/i,
    /\bbastard\b/i, /\bwhore\b/i, /\bdivorce\b/i, /\bpregnan/i, /\bdead\b/i, /\bdie\b/i,
    /\bkill(ed|ing)?\b/i ] },
  cliff_sfx: { w: 4.0, p: [
    /cliffhanger/i, /abrupt(ly)? cut/i, /sudden silence/i, /cuts? to black/i,
    /smash cut/i, /hard cut/i, /ominous/i, /sting(er)?\b/i, /swell(s|ing)?\b/i,
    /drone/i, /tension/i, /suspense/i, /freeze/i ] },
  cliff_camera: { w: 3.0, p: [
    /cut to black/i, /smash cut/i, /hard cut/i, /freeze frame/i, /holds? on/i,
    /push-?in/i, /slow(ly)? (zoom|push)/i, /lingers?/i, /turns? to (face|look)/i,
    /steps? into (the )?(light|frame|view)/i, /reveal(s|ing|ed)?/i, /widen(s|ing)?/i,
    /eyes widen/i ] },
  cliff_question: { w: 2.0, p: [/\?\s*"?\s*$/] },
};
const BONUS_SCENE_OPEN_S = 15, BONUS_SCENE_OPEN = 1.0;
const BONUS_EPISODE_END_S = 12, BONUS_EPISODE_END = 2.5, BONUS_AFTER_SFX = 1.0;
const DIALOGUE_DENSITY_WEIGHT = 2.0, NMS_IOU = 0.6;

const hits = (ps, t) => ps.reduce((n, r) => n + (r.test(t) ? 1 : 0), 0);

export function scoreBeat(beat, ep, prev) {
  const t = beat.text, emo = beat.emotions.join(" ");
  let hook = 0, structural = 0, cliff = 0;
  if (beat.kind === "ACTION" || beat.kind === "SOUND FX" || beat.kind === "CAMERA")
    hook += SIG.hook_action.w * hits(SIG.hook_action.p, t);
  if (beat.kind === "DIALOGUE") {
    hook += SIG.hook_action.w * 0.5 * hits(SIG.hook_action.p, t);
    hook += SIG.hook_emotion.w * hits(SIG.hook_emotion.p, emo);
    hook += SIG.hook_line.w * hits(SIG.hook_line.p, t);
    const words = (t.match(/\w+/g) || []).length;
    if (words >= 2 && words <= 6) hook += 1.0;
  }
  const sc = ep.scenes.find(s => s.number === beat.scene);
  if (sc && beat.start_s - sc.start_s <= BONUS_SCENE_OPEN_S) structural += BONUS_SCENE_OPEN;
  if (prev && prev.kind === "SOUND FX" && beat.kind === "DIALOGUE") structural += BONUS_AFTER_SFX;
  if (beat.kind === "SOUND FX") cliff += SIG.cliff_sfx.w * hits(SIG.cliff_sfx.p, t);
  if (beat.kind === "CAMERA") cliff += SIG.cliff_camera.w * hits(SIG.cliff_camera.p, t);
  if (beat.kind === "DIALOGUE") {
    cliff += SIG.cliff_question.w * hits(SIG.cliff_question.p, t);
    cliff += SIG.hook_line.w * 0.4 * hits(SIG.hook_line.p, t);
  }
  if (ep.duration - beat.end_s <= BONUS_EPISODE_END_S) cliff += BONUS_EPISODE_END;
  return [hook, structural, cliff];
}

const iou = (a, b) => {
  const inter = Math.max(0, Math.min(a.end_s, b.end_s) - Math.max(a.start_s, b.start_s));
  const uni = (a.end_s - a.start_s) + (b.end_s - b.start_s) - inter;
  return uni > 0 ? inter / uni : 0;
};

export function generateCandidates(ep, topN = 4) {
  const beats = ep.beats;
  if (!beats.length) return [];
  const sc = {};
  beats.forEach((b, i) => { sc[b.id] = scoreBeat(b, ep, i ? beats[i - 1] : null); });

  const windows = [];
  for (let i = 0; i < beats.length; i++) {
    const start = beats[i], cap = start.start_s + HOOK_WINDOW_S;
    const head = beats.slice(i).filter(b => b.start_s < cap);
    const hookContent = Math.max(0, ...head.map(b => sc[b.id][0]));
    if (hookContent <= 0) continue;
    const hookScore = hookContent + Math.max(0, ...head.map(b => sc[b.id][1]));

    let first = start, last = start;
    for (let j = i + 1; j < beats.length; j++) {
      const e = beats[j];
      if (e.start_s < first.start_s) first = e;
      if (e.end_s > last.end_s) last = e;
      const dur = last.end_s - first.start_s;
      if (dur < MIN_PROMO_S) continue;
      if (dur > MAX_PROMO_S) break;
      const inner = beats.slice(i, j + 1);
      const cliff = Math.max(...inner.slice(-3).map(b => sc[b.id][2]));
      const dialogue = inner.filter(b => b.kind === "DIALOGUE").length;
      const density = dialogue / Math.max(1, inner.length);
      const bodyAvg = inner.reduce((s2, b) => s2 + sc[b.id][0], 0) / Math.max(1, inner.length);
      windows.push({
        start_beat: first.id, end_beat: last.id,
        start_s: first.start_s, end_s: last.end_s,
        beat_ids: inner.map(b => b.id),
        hook_score: +hookScore.toFixed(3), cliff_score: +cliff.toFixed(3),
        heuristic_score: +(hookScore + cliff + bodyAvg + DIALOGUE_DENSITY_WEIGHT * density).toFixed(3),
        dialogue_density: +density.toFixed(3), n_beats: inner.length,
      });
    }
  }
  windows.sort((a, b) => b.heuristic_score - a.heuristic_score);
  const kept = [];
  for (const c of windows) {
    if (kept.every(k => iou(c, k) < NMS_IOU)) kept.push(c);
    if (kept.length >= topN) break;
  }
  return kept;
}
