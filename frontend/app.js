/* ── State ───────────────────────────────────────────────────── */
const state = {
  currentPersonaId: null,
  activePersona:    null,   // full persona object for the active character (for energy roll)
  conversation:     null,   // full conv JSON {conversation_id, persona_id, turns, ...}
  importedFields:   null,
  personaDirty:     false,
  panelFolded:      false,
};

let generating = false;  // guard against concurrent generation

/* ── Theme & font (applied before paint via inline script in <head>) ── */
function applyThemeUI(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  document.getElementById('icon-moon').style.display = theme === 'dark'  ? '' : 'none';
  document.getElementById('icon-sun').style.display  = theme === 'light' ? '' : 'none';
}
function applyFontUI(font) {
  document.documentElement.setAttribute('data-font', font);
  document.querySelectorAll('.font-ctrl .btn-icon').forEach(b => {
    b.classList.toggle('active-font', b.dataset.font === font);
  });
}

document.getElementById('btn-theme').addEventListener('click', () => {
  const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  localStorage.setItem('ct-theme', next);
  applyThemeUI(next);
});

document.querySelectorAll('.font-ctrl .btn-icon').forEach(btn => {
  btn.addEventListener('click', () => {
    const f = btn.dataset.font;
    localStorage.setItem('ct-font', f);
    applyFontUI(f);
  });
});

applyThemeUI(document.documentElement.getAttribute('data-theme') || 'dark');
applyFontUI(document.documentElement.getAttribute('data-font')   || 'md');

/* ── Helpers ─────────────────────────────────────────────────── */
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatMessage(raw) {
  const escaped = escapeHtml(raw);
  const withItalics = escaped.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  const parts = withItalics.split(/\n{2,}/);
  if (parts.length > 1) {
    return parts.map(p => '<p>' + p.replace(/\n/g, '<br>') + '</p>').join('');
  }
  return withItalics.replace(/\n/g, '<br>');
}

function setStatus(el, msg, type) {
  el.textContent = msg;
  el.className = 'status-line' + (type ? ' ' + type : '');
}

function showToast(msg, duration) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('visible');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('visible'), duration || 2500);
}

function relativeTime(ts) {
  if (!ts) return '';
  const diff = Date.now() / 1000 - ts;
  if (diff < 60)    return 'just now';
  if (diff < 3600)  return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function genId() {
  return Math.random().toString(16).slice(2, 14);
}

const FIELDS = [
  'identity','core_desires','standards','fears','coping_style',
  'beliefs_about_others','self_beliefs','tastes','relational_stance',
  'internal_tensions','temperament','voice','boundaries'
];

/* ── Nav ─────────────────────────────────────────────────────── */
document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('view-' + btn.dataset.view).classList.add('active');
    if (btn.dataset.view === 'persona')  loadPersonaList();
    if (btn.dataset.view === 'chat')     loadChatPersonaList();
    if (btn.dataset.view === 'settings') loadSettings();
  });
});

/* ── Chat: persona list & character name ─────────────────────── */
async function loadChatPersonaList() {
  const sel = document.getElementById('chat-persona-select');
  const prev = sel.value;
  sel.innerHTML = '<option value="">— select a character —</option>';
  const list = await fetch('/api/personas').then(r => r.json()).catch(() => []);
  list.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = p.identity || p.id;
    sel.appendChild(opt);
  });
  if (prev && list.find(p => p.id === prev)) {
    sel.value = prev;
    loadActivePersona(prev);
  } else if (list.length) {
    sel.value = list[0].id;
    state.currentPersonaId = list[0].id;
    updateCharName(list[0].identity || list[0].id, list[0].id);
    loadConvoList(list[0].id);
    loadActivePersona(list[0].id);
  } else {
    state.activePersona = null;
  }
}

function updateCharName(name, id) {
  const bar   = document.getElementById('chat-char-name');
  const label = document.getElementById('chat-char-label');
  if (id) {
    label.textContent = name;
    bar.classList.remove('hidden');
  } else {
    bar.classList.add('hidden');
  }
}

document.getElementById('chat-persona-select').addEventListener('change', function () {
  const newPid = this.value || null;
  state.currentPersonaId = newPid;
  const opt = this.options[this.selectedIndex];
  updateCharName(opt ? opt.textContent.trim() : '', this.value);
  loadActivePersona(newPid);

  const hasTurns = state.conversation
    && state.conversation.turns
    && state.conversation.turns.length > 0;

  if (!hasTurns) {
    // No conversation, or an empty one with no messages — rebind persona and refresh the list
    if (state.conversation) state.conversation.persona_id = newPid;
    if (newPid) loadConvoList(newPid);
    else renderConvoList([]);
    return;
  }

  // Active conversation with messages — start a fresh chat for the new character
  state.conversation = null;
  setSaveStatus(true);
  messagesEl.innerHTML = '';
  showEmpty('Select a character above to begin a conversation.');
  document.getElementById('inner-state-panel').innerHTML =
    '<p class="inner-placeholder">The character\'s private appraisal will appear here after each reply.</p>';
  if (newPid) loadConvoList(newPid);
  else renderConvoList([]);
});

async function loadActivePersona(pid) {
  if (!pid) { state.activePersona = null; return; }
  try {
    state.activePersona = await fetch('/api/personas/' + pid).then(r => r.json());
  } catch (e) {
    state.activePersona = null;
  }
}

/* ── Conversation store helpers ──────────────────────────────── */
function getCarryForward() {
  const turns = (state.conversation || {}).turns || [];
  for (let i = turns.length - 1; i >= 0; i--) {
    if (turns[i].committed && turns[i].variants && turns[i].variants.length) {
      const v = turns[i].variants[turns[i].chosen || 0];
      return {
        emotionalState: (v.intent || {}).emotional_state || null,
        agenda:         (v.intent || {}).agenda          || null,
      };
    }
  }
  return { emotionalState: null, agenda: null };
}

function getHistoryForApi() {
  const history = [];
  // Include scene opener as first character message so subsequent turns see it
  const openerText = state.conversation && state.conversation.scene_opener;
  if (openerText) {
    history.push({ role: 'character', content: openerText });
  }
  const turns = (state.conversation || {}).turns || [];
  for (const turn of turns) {
    if (!turn.committed) continue;
    if (!turn.variants || !turn.variants.length) continue;
    const v = turn.variants[turn.chosen || 0];
    history.push({ role: 'user',      content: turn.user });
    if (v && v.reply) history.push({ role: 'character', content: v.reply });
  }
  return history;
}

function setSaveStatus(ok) {
  const el = document.getElementById('save-status-indicator');
  if (!el) return;
  if (ok) {
    el.textContent = '';
    el.style.display = 'none';
  } else {
    el.textContent = 'Not saved to disk';
    el.style.display = '';
  }
}

async function saveConversation() {
  const conv = state.conversation;
  if (!conv) return;
  try {
    const res = await fetch('/api/conversations/' + conv.conversation_id, {
      method:  'PUT',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(conv),
    });
    if (!res.ok) {
      console.warn('Save returned HTTP', res.status);
      setSaveStatus(false);
    } else {
      setSaveStatus(true);
    }
  } catch (e) {
    console.warn('Save failed:', e);
    setSaveStatus(false);
  }
}

/* ── Conversation list ───────────────────────────────────────── */
async function loadConvoList(pid) {
  const list = await fetch('/api/conversations?persona_id=' + encodeURIComponent(pid))
    .then(r => r.json()).catch(() => []);
  renderConvoList(list);
}

function renderConvoList(list) {
  const el = document.getElementById('convo-list');
  if (!list.length) {
    el.innerHTML = '<div class="convo-list-empty">No past conversations yet.</div>';
    return;
  }
  el.innerHTML = '';
  const activeCid = state.conversation ? state.conversation.conversation_id : null;
  list.forEach(c => {
    const item = document.createElement('div');
    item.className = 'convo-item' + (c.conversation_id === activeCid ? ' active-convo' : '');
    item.dataset.cid = c.conversation_id;
    const n = c.turns;
    item.innerHTML =
      '<div class="convo-item-title">' + escapeHtml(c.title || '(empty)') + '</div>'
      + '<div class="convo-item-meta">'
      + escapeHtml(relativeTime(c.updated))
      + ' · ' + n + (n === 1 ? ' turn' : ' turns')
      + '</div>';
    item.addEventListener('click', () => openConversation(c.conversation_id));
    el.appendChild(item);
  });
}

async function openConversation(cid) {
  const data = await fetch('/api/conversations/' + cid).then(r => r.json()).catch(() => null);
  if (!data || data.error) return;

  state.conversation = data;
  if (data.persona_id) {
    const sel = document.getElementById('chat-persona-select');
    if (sel.value !== data.persona_id) {
      sel.value = data.persona_id;
      const selOpt = sel.options[sel.selectedIndex];
      updateCharName(selOpt ? selOpt.textContent.trim() : data.persona_id, data.persona_id);
      state.currentPersonaId = data.persona_id;
    }
    loadActivePersona(data.persona_id);
  }
  messagesEl.innerHTML = '';
  hideEmpty();
  hideSceneSetup();

  // Render scene opener if this was a scene conversation
  if (data.scene_opener) {
    const openerEl = document.createElement('div');
    openerEl.className = 'bubble character scene-opener';
    openerEl.innerHTML = formatMessage(data.scene_opener);
    messagesEl.appendChild(openerEl);
  }

  const turns = data.turns || [];
  turns.forEach((turn, idx) => {
    const isLastTurn = idx === turns.length - 1;
    const uncommitted = !turn.committed && isLastTurn;
    const variant = (turn.variants && turn.variants.length)
      ? turn.variants[turn.chosen || 0]
      : null;

    const charHtml = variant ? formatMessage(variant.reply) : null;
    const turnEl = appendTurnDOM(idx, turn.user, charHtml, !uncommitted);

    if (!uncommitted && turn.variants && turn.variants.length > 1) {
      const altNote = turnEl.querySelector('.alt-saved-note');
      if (altNote) {
        const n = turn.variants.length - 1;
        altNote.classList.remove('hidden');
        altNote.textContent = n + (n === 1 ? ' alternative saved' : ' alternatives saved');
      }
    }

    if (uncommitted) updateTurnControls(turnEl, idx);
  });

  if (turns.length) {
    const last = turns[turns.length - 1];
    if (last.variants && last.variants.length) {
      const v = last.variants[last.chosen || 0];
      if (v && v.intent) renderInnerState(v.intent, v.energy, v.inspirations);
    }
  } else if (data.scene && data.scene.objective) {
    // Scene conversation with opener but no turns yet — show objective
    renderInnerState(null, null, null);
  }

  scrollToBottom();
  document.querySelectorAll('.convo-item').forEach(el => {
    el.classList.toggle('active-convo', el.dataset.cid === cid);
  });
}

/* ── Chat: scroll helpers ────────────────────────────────────── */
const messagesEl   = document.getElementById('chat-messages');
const btnScrollBot = document.getElementById('btn-scroll-bottom');

function isNearBottom() {
  return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 100;
}
function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}
messagesEl.addEventListener('scroll', () => {
  btnScrollBot.classList.toggle('hidden', isNearBottom());
}, { passive: true });
btnScrollBot.addEventListener('click', scrollToBottom);

/* ── Chat: empty state ───────────────────────────────────────── */
function hideEmpty() {
  const el = document.getElementById('chat-empty');
  if (el) el.style.display = 'none';
}
function showEmpty(msg) {
  const el = document.getElementById('chat-empty');
  if (el) { el.textContent = msg || 'Select a character above to begin.'; el.style.display = ''; }
}

/* ── Chat: turn DOM ──────────────────────────────────────────── */
function appendTurnDOM(turnIdx, userMsg, charHtml, committed) {
  hideEmpty();
  const div = document.createElement('div');
  div.className = 'turn';
  div.dataset.turnIdx = String(turnIdx);

  const userBubble = document.createElement('div');
  userBubble.className = 'bubble user';
  userBubble.innerHTML = formatMessage(userMsg);
  div.appendChild(userBubble);

  const charWrap = document.createElement('div');
  charWrap.className = 'turn-char-wrap';

  if (charHtml !== null) {
    const charBubble = document.createElement('div');
    charBubble.className = 'bubble character';
    charBubble.innerHTML = charHtml;
    charWrap.appendChild(charBubble);
  }

  if (!committed) {
    const controls = document.createElement('div');
    controls.className = 'turn-controls';
    controls.innerHTML =
      '<div class="variant-nav hidden">'
      + '<button class="btn-icon-small btn-variant-prev" title="Previous variant">&#8249;</button>'
      + '<span class="variant-counter"></span>'
      + '<button class="btn-icon-small btn-variant-next" title="Next variant">&#8250;</button>'
      + '</div>'
      + '<button class="btn-regen btn-small">Regenerate</button>';
    controls.querySelector('.btn-regen').addEventListener('click', regenerate);
    controls.querySelector('.btn-variant-prev').addEventListener('click', () => cycleVariant(turnIdx, -1));
    controls.querySelector('.btn-variant-next').addEventListener('click', () => cycleVariant(turnIdx, 1));
    charWrap.appendChild(controls);
  }

  const altNote = document.createElement('div');
  altNote.className = 'alt-saved-note hidden';
  charWrap.appendChild(altNote);

  div.appendChild(charWrap);
  messagesEl.appendChild(div);
  if (isNearBottom()) scrollToBottom();
  return div;
}

function updateTurnControls(turnEl, turnIdx) {
  if (!state.conversation) return;
  const turn = state.conversation.turns[turnIdx];
  if (!turn) return;
  const varCount = turn.variants.length;
  const chosen   = turn.chosen || 0;

  const nav     = turnEl.querySelector('.variant-nav');
  const counter = turnEl.querySelector('.variant-counter');
  const prev    = turnEl.querySelector('.btn-variant-prev');
  const next    = turnEl.querySelector('.btn-variant-next');
  if (!nav) return;

  if (varCount > 1) {
    nav.classList.remove('hidden');
    counter.textContent = (chosen + 1) + ' / ' + varCount;
    prev.disabled = chosen === 0;
    next.disabled = chosen === varCount - 1;
  } else {
    nav.classList.add('hidden');
  }
}

function commitLastTurnDOM() {
  const turns   = state.conversation.turns;
  const lastIdx = turns.length - 1;
  const lastTurn = turns[lastIdx];
  const turnEl  = document.querySelector('.turn[data-turn-idx="' + lastIdx + '"]');
  if (!turnEl) return;

  const controls = turnEl.querySelector('.turn-controls');
  if (controls) controls.remove();

  if (lastTurn.variants.length > 1) {
    const altNote = turnEl.querySelector('.alt-saved-note');
    if (altNote) {
      const n = lastTurn.variants.length - 1;
      altNote.classList.remove('hidden');
      altNote.textContent = n + (n === 1 ? ' alternative saved' : ' alternatives saved');
    }
  }
}

function cycleVariant(turnIdx, delta) {
  if (!state.conversation) return;
  const turn = state.conversation.turns[turnIdx];
  if (!turn || turn.committed) return;
  const newChosen = (turn.chosen || 0) + delta;
  if (newChosen < 0 || newChosen >= turn.variants.length) return;
  turn.chosen = newChosen;

  const variant = turn.variants[newChosen];
  const turnEl  = document.querySelector('.turn[data-turn-idx="' + turnIdx + '"]');
  if (!turnEl) return;

  const charBubble = turnEl.querySelector('.bubble.character, .bubble.error-bubble');
  if (charBubble) {
    charBubble.className = 'bubble character';
    charBubble.innerHTML = formatMessage(variant.reply);
  }

  renderInnerState(variant.intent, variant.energy, variant.inspirations);
  updateTurnControls(turnEl, turnIdx);
  saveConversation();
}

/* ── Chat: inner state panel ─────────────────────────────────── */
function setInnerConsidering() {
  const panel = document.getElementById('inner-state-panel');
  panel.innerHTML =
    '<div class="inner-considering">'
    + '<span class="considering-dot"></span>'
    + '<span class="considering-dot"></span>'
    + '<span class="considering-dot"></span>'
    + '<span class="inner-considering-label">considering</span>'
    + '</div>'
    + '<div class="thought-stream" id="thought-stream-text"></div>';
}

function appendThoughtText(chunk) {
  const el = document.getElementById('thought-stream-text');
  if (el) {
    el.textContent += chunk;
    const panel = document.getElementById('inner-state-panel');
    panel.scrollTop = panel.scrollHeight;
  }
}

/* ── Appraisal label parsing ─────────────────────────────────── */
const APPRAISAL_LABELS = [
  ["USER'S WHY",      "user_emotional_why"],
  ["TOUCHED",         "touched"],
  ["APPRAISAL",       "appraisal"],
  ["TENSION LEVEL",   "tension_level"],
  ["TENSION",         "internal_tension"],
  ["GIVEN",           "given_read"],
  ["TRANSFORMATION",  "transformation"],
  ["EFFECTIVE",       "effective_read"],
  ["ACTION TENDENCY", "action_tendency"],
  ["PULL BACK",       "pull_back"],
  ["AGENDA",          "agenda"],
  ["COURSE A",        "course_a"],
  ["COURSE B",        "course_b"],
  ["CHOSEN MOVE",     "chosen_move"],
  ["OBJECTIVE STATUS","objective_status"],
  ["INITIATIVE",      "initiative"],
  ["CONNECTION",      "connection"],
  ["EMOTIONAL STATE", "emotional_state"],
];

function parseAppraisalText(raw) {
  const text = raw.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
  const intent = {};
  const positions = [];
  for (const [label] of APPRAISAL_LABELS) {
    const re = new RegExp('(?:^|\\n)\\s*' + label.replace(/'/g, "['']") + '\\s*:', 'i');
    const m = re.exec(text);
    if (m) positions.push({ label, pos: m.index + m[0].indexOf(m[0].trim()), end: m.index + m[0].length });
  }
  positions.sort((a, b) => a.pos - b.pos);
  for (let i = 0; i < positions.length; i++) {
    const { label, end } = positions[i];
    const nextPos = i + 1 < positions.length ? positions[i + 1].pos : text.length;
    const value = text.slice(end, nextPos).trim();
    const key = APPRAISAL_LABELS.find(([l]) => l === label)?.[1];
    if (key) intent[key] = value;
  }
  const rawConn = String(intent.connection || '').toLowerCase().trim();
  if (/\bconnect\b/.test(rawConn))     intent.connection = 'connect';
  else if (/\bresist\b/.test(rawConn)) intent.connection = 'resist';
  else                                  intent.connection = 'conflicted';
  const rawTL = String(intent.tension_level || '').toLowerCase();
  const matchedLevel = (rawTL.match(/\b(none|mild|moderate|strong)\b/) || [])[1] || 'none';
  intent.tension_level = matchedLevel;
  const rawInit = String(intent.initiative || '').toLowerCase();
  const matchedInit = (rawInit.match(/\b(yield|nudge|lead)\b/) || [])[1] || 'nudge';
  intent.initiative = matchedInit;
  const rawOS = String(intent.objective_status || '').toLowerCase();
  const matchedOS = (rawOS.match(/\b(pursuing|advanced|stalled|achieved|blocked)\b/) || [])[1] || 'pursuing';
  intent.objective_status = matchedOS;
  return intent;
}

function tensionMeter(level, desc) {
  const levels = ['none', 'mild', 'moderate', 'strong'];
  const idx = Math.max(0, levels.indexOf(level));
  const pips = [0, 1, 2].map(i =>
    '<span class="t-pip' + (i < idx ? ' t-filled' : '') + '"></span>'
  ).join('');
  const descHtml = (desc && desc !== 'none' && level !== 'none')
    ? '<div class="tension-desc">' + escapeHtml(String(desc)) + '</div>'
    : '';
  return '<div class="tension-meter-wrap">'
    + '<div class="is-label">Tension</div>'
    + '<div class="tension-meter-row">'
    + '<div class="tension-pips">' + pips + '</div>'
    + '<span class="tension-level-label">' + escapeHtml(level || 'none') + '</span>'
    + '</div>'
    + descHtml
    + '</div>';
}

function initiativeIndicator(level) {
  const levels = ['yield', 'nudge', 'lead'];
  const steps = levels.map(l =>
    '<span class="init-step init-' + l + (l === level ? ' init-active' : '') + '">' + l + '</span>'
  ).join('');
  return '<div class="initiative-wrap">'
    + '<div class="is-label">Initiative</div>'
    + '<div class="initiative-indicator">' + steps + '</div>'
    + '</div>';
}

function drewOnSection(inspirations) {
  if (!inspirations || !inspirations.length) return '';
  const items = inspirations.map(ins => {
    const mag = (ins.magnitude || '').toLowerCase();
    return '<div class="drew-on-item">'
      + '<span class="drew-on-mag drew-on-mag-' + escapeHtml(mag) + '">'
      + escapeHtml((ins.magnitude || '?').toUpperCase())
      + '</span>'
      + '<span class="drew-on-action">' + escapeHtml(ins.action || '') + '</span>'
      + '</div>';
  }).join('');
  return '<div class="drew-on-section">'
    + '<div class="is-label">Drew on</div>'
    + items
    + '</div>';
}

function renderCourses(courseA, courseB, chosenMove) {
  if (!courseA && !courseB) return '';
  const cm = (chosenMove || '').toLowerCase();
  const aChosen = cm.includes('course a');
  const bChosen = cm.includes('course b');
  let html = '<div class="is-courses">';
  if (courseA) {
    html += '<div class="is-course' + (aChosen ? ' is-course-chosen' : '') + '">'
      + '<div class="is-course-label">Course A'
      + (aChosen ? '<span class="is-chosen-tag">chosen</span>' : '')
      + '</div>'
      + '<div class="is-course-text">' + escapeHtml(courseA) + '</div>'
      + '</div>';
  }
  if (courseB) {
    html += '<div class="is-course' + (bChosen ? ' is-course-chosen' : '') + '">'
      + '<div class="is-course-label">Course B'
      + (bChosen ? '<span class="is-chosen-tag">chosen</span>' : '')
      + '</div>'
      + '<div class="is-course-text">' + escapeHtml(courseB) + '</div>'
      + '</div>';
  }
  html += '</div>';
  if (!aChosen && !bChosen && chosenMove) {
    html += '<div class="is-section">'
      + '<div class="is-label">Chosen move</div>'
      + '<div class="is-value">' + escapeHtml(chosenMove) + '</div>'
      + '</div>';
  }
  return html;
}

function energyBadge(energy) {
  if (!energy) return '';
  const level = energy.level || 'measured';
  const note  = energy.note  || '';
  const disp  = (state.activePersona || {}).assertiveness || 'balanced';
  const dispLabel = ASSERTIVENESS_LABELS[disp] || 'Balanced';
  return '<div class="energy-badge-wrap">'
    + '<div class="is-label">Energy <span class="energy-disp-tag">' + escapeHtml(dispLabel) + '</span></div>'
    + '<span class="energy-badge energy-' + escapeHtml(level) + '">' + escapeHtml(level) + '</span>'
    + (note ? '<div class="energy-note">' + escapeHtml(note) + '</div>' : '')
    + '</div>';
}

function _objectiveStatusHtml(scene, objectiveStatus) {
  if (!scene || !scene.objective) return '';
  const obj    = scene.objective;
  const status = objectiveStatus || 'pursuing';
  return '<div class="is-objective-wrap">'
    + '<div class="is-objective-header">'
    + '<div class="is-label">Objective</div>'
    + '<span class="obj-status obj-status-' + escapeHtml(status) + '">' + escapeHtml(status) + '</span>'
    + '</div>'
    + '<div class="is-objective-text">' + escapeHtml(obj.objective || '') + '</div>'
    + (obj.obstacle ? '<div class="is-objective-obstacle">obstacle: ' + escapeHtml(obj.obstacle) + '</div>' : '')
    + '</div>';
}

function renderInnerState(intent, energy, inspirations) {
  const panel = document.getElementById('inner-state-panel');
  const scene = state.conversation && state.conversation.scene;
  const objHtml = _objectiveStatusHtml(scene, intent && intent.objective_status);

  if (!intent || typeof intent !== 'object') {
    if (objHtml) {
      panel.classList.remove('inner-fade-in');
      void panel.offsetWidth;
      panel.innerHTML = objHtml;
      panel.classList.add('inner-fade-in');
    } else {
      panel.innerHTML = '<p class="inner-placeholder">No appraisal data returned.</p>';
    }
    return;
  }
  const conn = intent.connection === 'connect' ? 'connect'
             : intent.connection === 'resist'  ? 'resist'
             : 'conflicted';

  function field(label, value) {
    if (!value || value === 'none') return '';
    return '<div class="is-section">'
      + '<div class="is-label">' + escapeHtml(label) + '</div>'
      + '<div class="is-value">'  + escapeHtml(String(value)) + '</div>'
      + '</div>';
  }
  function group(title, content) {
    if (!content.trim()) return '';
    return '<details class="is-group" open>'
      + '<summary>' + escapeHtml(title) + '</summary>'
      + '<div class="is-group-body">' + content + '</div>'
      + '</details>';
  }

  const agendaHtml = intent.agenda
    ? '<div class="is-section is-agenda">'
      + '<div class="is-label">Agenda</div>'
      + '<div class="is-agenda-text">' + escapeHtml(intent.agenda) + '</div>'
      + '</div>'
    : '';

  const html =
    objHtml
    + '<span class="is-connection ' + conn + '">' + conn.toUpperCase() + '</span>'
    + '<div class="is-state">' + escapeHtml(intent.emotional_state || '—') + '</div>'
    + group('Perception',
        field('What the user is seeking', intent.user_emotional_why)
        + field('What was activated', intent.touched))
    + group('Appraisal',
        field('Appraisal', intent.appraisal)
        + tensionMeter(intent.tension_level || 'none', intent.internal_tension))
    + group('Reaction',
        field('Raw reaction', intent.given_read)
        + field('Transformed by relationship', intent.transformation)
        + field('Effective reaction', intent.effective_read))
    + group('Plan',
        initiativeIndicator(intent.initiative || 'nudge')
        + energyBadge(energy)
        + agendaHtml
        + field('Action tendency', intent.action_tendency)
        + field('Holding back', intent.pull_back)
        + renderCourses(intent.course_a, intent.course_b, intent.chosen_move)
        + drewOnSection(inspirations))
    + _buildSceneFactsHtml();

  panel.classList.remove('inner-fade-in');
  void panel.offsetWidth;
  panel.innerHTML = html;
  panel.classList.add('inner-fade-in');
}

/* ── Chat: composer helpers ──────────────────────────────────── */
function setComposerEnabled(enabled) {
  const input = document.getElementById('chat-input');
  const btn   = document.getElementById('btn-send');
  input.disabled = !enabled;
  btn.disabled   = !enabled;
}

const chatInput = document.getElementById('chat-input');
chatInput.addEventListener('input', () => {
  chatInput.style.height = 'auto';
  chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + 'px';
});

/* ── Scene setup state ───────────────────────────────────────── */
let _scene = { genre: 'romance', seeds: null, objectives: null, chosen: null };

const _SEED_KEYS = ['location', 'time_of_day', 'weather', 'mood', 'situation'];
let _seedOptionsLoaded = false;

async function _ensureSeedOptions() {
  if (_seedOptionsLoaded) return;
  const opts = await fetch('/api/scene/options').then(r => r.json());
  _SEED_KEYS.forEach(k => {
    const sel = document.getElementById('seed-' + k);
    if (!sel) return;
    sel.innerHTML = (opts[k] || []).map(v =>
      '<option value="' + escapeHtml(v) + '">' + escapeHtml(v) + '</option>'
    ).join('');
  });
  _seedOptionsLoaded = true;
}

function _setSeedDropdowns(seeds) {
  _SEED_KEYS.forEach(k => {
    const sel = document.getElementById('seed-' + k);
    if (sel && seeds[k]) sel.value = seeds[k];
  });
}

function _getSeedsFromDropdowns() {
  const out = {};
  _SEED_KEYS.forEach(k => {
    const sel = document.getElementById('seed-' + k);
    out[k] = sel ? sel.value : '';
  });
  return out;
}

function _genreLabel() {
  const custom = document.getElementById('scene-genre-custom');
  return (custom && custom.value.trim()) || _scene.genre;
}

function _objectiveForApi() {
  const custom = document.getElementById('scene-obj-custom');
  if (custom && custom.value.trim()) return custom.value.trim();
  if (_scene.chosen) {
    return _scene.chosen.obstacle
      ? _scene.chosen.objective + ' (obstacle: ' + _scene.chosen.obstacle + ')'
      : _scene.chosen.objective;
  }
  return null;
}

function _updateBeginButton() {
  const btn     = document.getElementById('btn-begin-scene');
  if (!btn) return;
  const custom  = document.getElementById('scene-obj-custom');
  const hasObj  = !!(_scene.chosen || (custom && custom.value.trim()));
  btn.disabled  = !(hasObj && _scene.seeds);
}

function showSceneSetup() {
  document.getElementById('scene-setup').classList.remove('hidden');
  document.getElementById('chat-messages').classList.add('hidden');
  document.getElementById('btn-scroll-bottom').classList.add('hidden');
  setComposerEnabled(false);
  if (!_scene.seeds) rollScene();
}

function hideSceneSetup() {
  document.getElementById('scene-setup').classList.add('hidden');
  document.getElementById('chat-messages').classList.remove('hidden');
  setComposerEnabled(true);
}

async function rollScene() {
  try {
    await _ensureSeedOptions();
    const seeds = await fetch('/api/scene/roll').then(r => r.json());
    _scene.seeds = seeds;
    _setSeedDropdowns(seeds);
    // Reset objectives when scene changes
    _scene.objectives = null;
    _scene.chosen = null;
    const objArea = document.getElementById('scene-objectives-area');
    if (objArea) {
      objArea.innerHTML = '<button id="btn-generate-objectives" class="btn-small">Generate objectives…</button>';
      document.getElementById('btn-generate-objectives').addEventListener('click', generateObjectives);
    }
    const rerollBtn = document.getElementById('btn-reroll-objectives');
    if (rerollBtn) rerollBtn.classList.add('hidden');
    const customObj = document.getElementById('scene-obj-custom');
    if (customObj) customObj.value = '';
    _updateBeginButton();
  } catch (e) {
    setStatus(document.getElementById('scene-status'), 'Could not load scene — is the server running?', 'err');
  }
}

async function generateObjectives() {
  const pid = document.getElementById('chat-persona-select').value;
  if (!pid) {
    setStatus(document.getElementById('scene-status'), 'Select a character first.', 'err');
    return;
  }
  if (!_scene.seeds) {
    setStatus(document.getElementById('scene-status'), 'Roll a scene first.', 'err');
    return;
  }
  const area = document.getElementById('scene-objectives-area');
  if (area) area.innerHTML = '<span class="scene-chip-loading">Generating objectives…</span>';
  setStatus(document.getElementById('scene-status'), '', '');
  try {
    const res = await fetch('/api/objectives/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ persona_id: pid, genre: _genreLabel(), seeds: _getSeedsFromDropdowns() }),
    }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    _scene.objectives = res.objectives || [];
    _scene.chosen = null;
    renderObjectives(_scene.objectives);
    const rerollBtn = document.getElementById('btn-reroll-objectives');
    if (rerollBtn) rerollBtn.classList.remove('hidden');
  } catch (e) {
    if (area) {
      area.innerHTML = '<button id="btn-generate-objectives" class="btn-small">Generate objectives…</button>';
      document.getElementById('btn-generate-objectives').addEventListener('click', generateObjectives);
    }
    setStatus(document.getElementById('scene-status'), 'Generate failed: ' + e.message, 'err');
  }
}

function renderObjectives(objectives) {
  const area = document.getElementById('scene-objectives-area');
  if (!area) return;
  if (!objectives || !objectives.length) {
    area.innerHTML = '<p class="scene-hint">No objectives returned — try rerolling.</p>';
    return;
  }
  area.innerHTML = objectives.map((o, i) =>
    '<div class="scene-obj-card" data-idx="' + i + '">'
    + '<div class="scene-obj-text">' + escapeHtml(o.objective || '') + '</div>'
    + '<div class="scene-obj-meta">'
    + (o.obstacle ? '<span class="scene-obj-obstacle">obstacle: ' + escapeHtml(o.obstacle) + '</span>' : '')
    + (o.desire   ? '<span class="scene-obj-desire">desire: '   + escapeHtml(o.desire)   + '</span>' : '')
    + '</div></div>'
  ).join('');
  area.querySelectorAll('.scene-obj-card').forEach(card => {
    card.addEventListener('click', () => {
      area.querySelectorAll('.scene-obj-card').forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      _scene.chosen = objectives[parseInt(card.dataset.idx, 10)];
      const customObj = document.getElementById('scene-obj-custom');
      if (customObj) customObj.value = '';
      _updateBeginButton();
    });
  });
}

async function beginScene(forcedObjective) {
  const pid = document.getElementById('chat-persona-select').value;
  if (!pid) { setStatus(document.getElementById('scene-status'), 'Select a character first.', 'err'); return; }
  if (!_scene.seeds) { setStatus(document.getElementById('scene-status'), 'Roll a scene first.', 'err'); return; }

  const objStr   = forcedObjective || _objectiveForApi() || 'explore what passes between them';
  const objObj   = _scene.chosen || { objective: objStr, obstacle: '', desire: '' };
  const genre    = _genreLabel();
  const seeds    = _getSeedsFromDropdowns();
  const statusEl = document.getElementById('scene-status');

  state.conversation = {
    conversation_id: genId(),
    persona_id:      pid,
    created:         Date.now() / 1000,
    updated:         Date.now() / 1000,
    title:           '(scene)',
    turns:           [],
    scene_facts:     [],
    scene: { genre, seeds, objective: objObj },
    scene_opener: '',
  };

  hideSceneSetup();
  hideEmpty();
  setStatus(document.getElementById('chat-status'), '', '');
  document.getElementById('inner-state-panel').innerHTML =
    '<p class="inner-placeholder">The character\'s private appraisal will appear here after each reply.</p>';
  document.querySelectorAll('.convo-item').forEach(el => el.classList.remove('active-convo'));

  generating = true;
  setComposerEnabled(false);

  const openerEl = document.createElement('div');
  openerEl.className = 'bubble character scene-opener';
  openerEl.innerHTML = '<span class="thinking-dots"><span>•</span><span>•</span><span>•</span></span>';
  messagesEl.appendChild(openerEl);
  scrollToBottom();

  try {
    const res = await fetch('/api/scene/open', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ persona_id: pid, genre, seeds, objective: objStr }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Scene open failed (' + res.status + ')');
    }

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let   text    = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      text += decoder.decode(value, { stream: true });
      openerEl.innerHTML = formatMessage(text) + '<span class="cursor"></span>';
      if (isNearBottom()) scrollToBottom();
    }
    if (!text) throw new Error('No opening received — check that the model is running.');
    openerEl.innerHTML = formatMessage(text);

    state.conversation.scene_opener = text;

    // Seed the ledger from the opener so early scene details are captured
    await extractAndUpdateFacts(
      { user: '', variants: [{ reply: text }], chosen: 0 },
      state.conversation
    );

    // Show objective in inner-state panel
    renderInnerState(null, null, null);

    await saveConversation();
    scrollToBottom();
    if (pid) loadConvoList(pid);

  } catch (e) {
    openerEl.className = 'bubble error-bubble scene-opener';
    openerEl.textContent = e.message;
    setStatus(document.getElementById('chat-status'), 'Scene open failed — ' + e.message, 'err');
    state.conversation = null;
  } finally {
    generating = false;
    setComposerEnabled(true);
    chatInput.focus();
  }
}

async function letCharacterDecide() {
  const pid = document.getElementById('chat-persona-select').value;
  if (!pid) { setStatus(document.getElementById('scene-status'), 'Select a character first.', 'err'); return; }

  setStatus(document.getElementById('scene-status'), 'Generating…', '');

  if (!_scene.seeds) await rollScene();

  let objectives = _scene.objectives;
  if (!objectives || !objectives.length) {
    try {
      const res = await fetch('/api/objectives/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ persona_id: pid, genre: _genreLabel(), seeds: _getSeedsFromDropdowns() }),
      }).then(r => r.json());
      objectives = (res.objectives || []);
      _scene.objectives = objectives;
    } catch (_) { objectives = []; }
  }

  _scene.chosen = objectives[0] || null;
  setStatus(document.getElementById('scene-status'), '', '');
  await beginScene();
}

async function _refreshObjective(status) {
  const conv = state.conversation;
  if (!conv || !conv.scene) return;
  const { genre, seeds, objective: oldObj } = conv.scene;
  const pid     = conv.persona_id;
  const context = status === 'achieved'
    ? 'The previous objective was achieved: "' + (oldObj && oldObj.objective || '') + '"'
    : 'The previous objective was blocked: "'  + (oldObj && oldObj.objective || '') + '"';

  const panel  = document.getElementById('inner-state-panel');
  const notice = document.createElement('div');
  notice.className = 'new-obj-notice';
  notice.textContent = 'Objective ' + status + ' — generating new one…';
  panel.prepend(notice);

  try {
    const res = await fetch('/api/objectives/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ persona_id: pid, genre, seeds, context }),
    }).then(r => r.json());
    if (res.objectives && res.objectives.length) {
      conv.scene.objective = res.objectives[0];
      notice.className = 'new-obj-notice new-obj-notice-done';
      notice.textContent = 'New objective: ' + res.objectives[0].objective;
      await saveConversation();
    }
  } catch (e) {
    notice.className = 'new-obj-notice new-obj-notice-err';
    notice.textContent = 'Could not refresh objective.';
  }
}

/* ── Chat: new conversation ──────────────────────────────────── */
document.getElementById('btn-new-chat').addEventListener('click', () => {
  state.conversation = null;
  _scene.objectives  = null;
  _scene.chosen      = null;
  messagesEl.innerHTML = '';
  setStatus(document.getElementById('chat-status'), '', '');
  setSaveStatus(true);
  document.querySelectorAll('.convo-item').forEach(el => el.classList.remove('active-convo'));
  showSceneSetup();
});

/* ── Scene setup: event wiring ──────────────────────────────── */
document.getElementById('btn-reroll-scene').addEventListener('click', rollScene);
document.getElementById('btn-begin-scene').addEventListener('click', () => beginScene());
document.getElementById('btn-let-char-decide').addEventListener('click', letCharacterDecide);
document.getElementById('btn-reroll-objectives').addEventListener('click', generateObjectives);
document.getElementById('btn-generate-objectives').addEventListener('click', generateObjectives);

// Genre button selection
document.getElementById('scene-genre-row').addEventListener('click', e => {
  const btn = e.target.closest('.btn-genre');
  if (!btn) return;
  document.querySelectorAll('.btn-genre').forEach(b => b.classList.remove('active-genre'));
  btn.classList.add('active-genre');
  _scene.genre = btn.dataset.genre;
  const customInput = document.getElementById('scene-genre-custom');
  if (customInput) customInput.value = '';
});

document.getElementById('scene-obj-custom').addEventListener('input', _updateBeginButton);

/* ── Chat: per-turn energy roll ──────────────────────────────── */
const ENERGY_NOTES = {
  restrained: 'a quiet, low-key turn; a small or holding move fits',
  measured:   'an ordinary turn; a modest, purposeful move',
  assertive:  'an energized turn; a forward, sized-up move',
  bold:       'a charged turn; a strong, scene-shifting move',
};

const ASSERTIVENESS = {
  meek:          { restrained: 40, measured: 30, assertive: 20, bold: 10 },
  laid_back:     { restrained: 25, measured: 40, assertive: 20, bold: 15 },
  balanced:      { restrained: 20, measured: 30, assertive: 30, bold: 20 },
  strong_willed: { restrained: 15, measured: 20, assertive: 40, bold: 25 },
  dominant:      { restrained: 10, measured: 20, assertive: 30, bold: 40 },
};

const ASSERTIVENESS_LABELS = {
  meek: 'Meek', laid_back: 'Laid-back', balanced: 'Balanced',
  strong_willed: 'Strong-willed', dominant: 'Dominant',
};

function rollEnergy(disposition) {
  const dist = ASSERTIVENESS[disposition] || ASSERTIVENESS.balanced;
  const total = Object.values(dist).reduce((a, b) => a + b, 0);
  let r = Math.random() * total, acc = 0;
  for (const [level, w] of Object.entries(dist)) {
    acc += w;
    if (r <= acc) return { level, note: ENERGY_NOTES[level] };
  }
  return { level: 'measured', note: ENERGY_NOTES.measured };
}

/* ── Scene-fact ledger helpers ───────────────────────────────── */
function formatSceneFacts(facts) {
  if (!facts || !facts.length) return 'No established facts yet.';
  return facts.map(f => '• ' + f).join('\n');
}

async function extractAndUpdateFacts(turn, conv) {
  if (!conv) return;
  const variant = turn.variants && turn.variants[turn.chosen || 0];
  const reply   = (variant && variant.reply) || '';
  if (!reply) return;
  try {
    const res = await fetch('/api/scene/facts', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        existing_facts: conv.scene_facts || [],
        user_message:   turn.user || '',
        reply:          reply,
      }),
    }).then(r => r.json());
    if (Array.isArray(res.scene_facts)) {
      conv.scene_facts = res.scene_facts;
    }
  } catch (e) {
    console.warn('[scene-facts] extraction failed:', e);
  }
}

function _buildSceneFactsHtml() {
  const facts = (state.conversation || {}).scene_facts;
  if (!facts || !facts.length) return '';
  const count = facts.length;
  const items = facts.map(f =>
    '<li class="scene-fact-item">' + escapeHtml(f) + '</li>'
  ).join('');
  return '<details class="is-group">'
    + '<summary>Scene facts <span class="scene-facts-count">(' + count + ')</span></summary>'
    + '<div class="is-group-body"><ul class="scene-facts-list">' + items + '</ul></div>'
    + '</details>';
}

/* ── Chat: shared two-pass generation ───────────────────────── */
async function runTwoPasses(pid, userMsg, history, carryForward, moveEnergy, objective, sceneFacts) {
  const appRes = await fetch('/api/appraise', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      persona_id:      pid,
      history:         history,
      user_message:    userMsg,
      emotional_state: carryForward.emotionalState,
      agenda:          carryForward.agenda,
      move_energy:     moveEnergy ? (moveEnergy.level + ': ' + moveEnergy.note) : undefined,
      objective:       objective || undefined,
      scene_facts:     formatSceneFacts(sceneFacts),
    }),
  });
  if (!appRes.ok) {
    const err = await appRes.json().catch(() => ({}));
    throw new Error(err.error || 'Appraisal failed (' + appRes.status + ')');
  }

  // Read retrieved inspirations from response header (available before stream body)
  let inspirations = [];
  try {
    const raw = appRes.headers.get('X-Inspirations');
    if (raw) inspirations = JSON.parse(raw);
  } catch (_) {}

  const appReader  = appRes.body.getReader();
  const appDecoder = new TextDecoder();
  let   appText    = '';
  while (true) {
    const { value, done } = await appReader.read();
    if (done) break;
    const chunk = appDecoder.decode(value, { stream: true });
    appText += chunk;
    appendThoughtText(chunk);
  }

  const intent = parseAppraisalText(appText);
  renderInnerState(intent, moveEnergy, inspirations);

  const streamRes = await fetch('/api/respond', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      persona_id:   pid,
      history:      history,
      user_message: userMsg,
      intent:       intent,
      scene_facts:  formatSceneFacts(sceneFacts),
    }),
  });
  if (!streamRes.ok) {
    const err = await streamRes.json().catch(() => ({}));
    throw new Error(err.error || 'Reply failed (' + streamRes.status + ')');
  }

  return { intent, streamRes, inspirations };
}

/* ── Chat: send message ──────────────────────────────────────── */
async function sendMessage() {
  if (generating) return;
  const statusEl = document.getElementById('chat-status');
  const msg = chatInput.value.trim();
  if (!msg) return;

  const pid = document.getElementById('chat-persona-select').value;
  if (!pid) { setStatus(statusEl, 'Select a character first.', 'err'); return; }

  if (!state.conversation) {
    state.conversation = {
      conversation_id: genId(),
      persona_id:      pid,
      created:         Date.now() / 1000,
      updated:         Date.now() / 1000,
      title:           msg.slice(0, 50),
      turns:           [],
      scene_facts:     [],
    };
  } else if (!state.conversation.persona_id) {
    // "New Chat" was clicked before a persona was selected; bind the persona now.
    state.conversation.persona_id = pid;
  }

  const conv  = state.conversation;
  const turns = conv.turns;

  // Commit the previous uncommitted turn, if any; extract its scene facts first
  if (turns.length && !turns[turns.length - 1].committed) {
    const prevTurn = turns[turns.length - 1];
    prevTurn.committed = true;
    commitLastTurnDOM();
    await extractAndUpdateFacts(prevTurn, conv);
    await saveConversation();
  }

  const cf      = getCarryForward();
  const history = getHistoryForApi();

  const newTurn = { user: msg, committed: false, chosen: 0, variants: [] };
  turns.push(newTurn);
  const turnIdx = turns.length - 1;

  chatInput.value = '';
  chatInput.style.height = 'auto';
  generating = true;
  setComposerEnabled(false);
  setStatus(statusEl, '', '');

  const turnEl   = appendTurnDOM(turnIdx, msg, null, false);
  const charWrap = turnEl.querySelector('.turn-char-wrap');

  const thinkEl = document.createElement('div');
  thinkEl.className = 'bubble thinking';
  thinkEl.innerHTML = '<span class="thinking-dots"><span>•</span><span>•</span><span>•</span></span>';
  charWrap.insertBefore(thinkEl, charWrap.querySelector('.turn-controls'));

  setInnerConsidering();

  const energy    = rollEnergy(state.activePersona?.assertiveness);
  const sceneObj  = conv.scene && conv.scene.objective;
  const objective = sceneObj
    ? (sceneObj.obstacle
        ? sceneObj.objective + ' (obstacle: ' + sceneObj.obstacle + ')'
        : sceneObj.objective)
    : undefined;

  try {
    const { intent, streamRes, inspirations } = await runTwoPasses(conv.persona_id, msg, history, cf, energy, objective, conv.scene_facts);

    thinkEl.className = 'bubble character';
    thinkEl.innerHTML = '';

    const streamReader  = streamRes.body.getReader();
    const streamDecoder = new TextDecoder();
    let   replyText     = '';
    while (true) {
      const { value, done } = await streamReader.read();
      if (done) break;
      replyText += streamDecoder.decode(value, { stream: true });
      thinkEl.innerHTML = formatMessage(replyText) + '<span class="cursor"></span>';
      if (isNearBottom()) scrollToBottom();
    }

    if (!replyText) throw new Error('No reply received. Check that Ollama is running.');
    thinkEl.innerHTML = formatMessage(replyText);

    newTurn.variants.push({ intent, reply: replyText, t: Date.now() / 1000, energy: { level: energy.level, note: energy.note }, inspirations });
    newTurn.chosen = 0;

    updateTurnControls(turnEl, turnIdx);
    await saveConversation();

    // Objective lifecycle: achieved or blocked → form a fresh objective
    if (conv.scene && conv.scene.objective
        && (intent.objective_status === 'achieved' || intent.objective_status === 'blocked')) {
      _refreshObjective(intent.objective_status);  // async, runs in background
    }

    scrollToBottom();
    if (state.currentPersonaId) loadConvoList(state.currentPersonaId);

  } catch (e) {
    // Remove controls from orphaned turn, show error in DOM, remove from state
    const controls = turnEl.querySelector('.turn-controls');
    if (controls) controls.remove();
    thinkEl.className = 'bubble error-bubble';
    thinkEl.textContent = e.message || 'Something went wrong.';
    turnEl.removeAttribute('data-turn-idx');
    turns.splice(turnIdx, 1);
    setStatus(statusEl, 'Error — check that Ollama is running and the character is saved.', 'err');
    scrollToBottom();
  } finally {
    generating = false;
    setComposerEnabled(true);
    chatInput.focus();
  }
}

document.getElementById('btn-send').addEventListener('click', sendMessage);
chatInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

/* ── Chat: regenerate ────────────────────────────────────────── */
async function regenerate() {
  if (generating) return;
  const conv = state.conversation;
  if (!conv || !conv.turns.length) return;

  const turns    = conv.turns;
  const lastTurn = turns[turns.length - 1];
  if (lastTurn.committed) return;

  const pid     = conv.persona_id;
  const msg     = lastTurn.user;
  const cf      = getCarryForward();
  const history = getHistoryForApi();
  const turnIdx = turns.length - 1;

  const turnEl   = document.querySelector('.turn[data-turn-idx="' + turnIdx + '"]');
  if (!turnEl) return;

  const charWrap = turnEl.querySelector('.turn-char-wrap');
  let charBubble = charWrap.querySelector('.bubble.character, .bubble.error-bubble');
  if (charBubble) {
    charBubble.className = 'bubble thinking';
    charBubble.innerHTML = '<span class="thinking-dots"><span>•</span><span>•</span><span>•</span></span>';
  } else {
    charBubble = document.createElement('div');
    charBubble.className = 'bubble thinking';
    charBubble.innerHTML = '<span class="thinking-dots"><span>•</span><span>•</span><span>•</span></span>';
    charWrap.insertBefore(charBubble, charWrap.querySelector('.turn-controls'));
  }

  generating = true;
  setComposerEnabled(false);
  setInnerConsidering();

  const energy    = rollEnergy(state.activePersona?.assertiveness);
  const regenSceneObj = conv.scene && conv.scene.objective;
  const regenObjective = regenSceneObj
    ? (regenSceneObj.obstacle
        ? regenSceneObj.objective + ' (obstacle: ' + regenSceneObj.obstacle + ')'
        : regenSceneObj.objective)
    : undefined;

  try {
    const { intent, streamRes, inspirations } = await runTwoPasses(pid, msg, history, cf, energy, regenObjective, conv.scene_facts);

    charBubble.className = 'bubble character';
    charBubble.innerHTML = '';

    const streamReader  = streamRes.body.getReader();
    const streamDecoder = new TextDecoder();
    let   replyText     = '';
    while (true) {
      const { value, done } = await streamReader.read();
      if (done) break;
      replyText += streamDecoder.decode(value, { stream: true });
      charBubble.innerHTML = formatMessage(replyText) + '<span class="cursor"></span>';
      if (isNearBottom()) scrollToBottom();
    }

    if (!replyText) throw new Error('No reply received.');
    charBubble.innerHTML = formatMessage(replyText);

    lastTurn.variants.push({ intent, reply: replyText, t: Date.now() / 1000, energy: { level: energy.level, note: energy.note }, inspirations });
    lastTurn.chosen = lastTurn.variants.length - 1;

    updateTurnControls(turnEl, turnIdx);
    await saveConversation();
    scrollToBottom();

  } catch (e) {
    charBubble.className = 'bubble error-bubble';
    charBubble.textContent = e.message || 'Regeneration failed.';
  } finally {
    generating = false;
    setComposerEnabled(true);
    chatInput.focus();
  }
}

/* ── Panel fold ──────────────────────────────────────────────── */
function applyPanelFold(folded) {
  state.panelFolded = folded;
  document.querySelector('.chat-right').classList.toggle('panel-folded', folded);
  document.querySelector('.chat-layout').classList.toggle('panel-folded', folded);
  const btn = document.getElementById('btn-fold-panel');
  if (btn) {
    btn.textContent = folded ? '›' : '‹';
    btn.title = folded ? 'Show inner state' : 'Hide inner state';
  }
  localStorage.setItem('ct-panel-folded', folded ? '1' : '0');
}

document.getElementById('btn-fold-panel').addEventListener('click', () => {
  applyPanelFold(!state.panelFolded);
});

applyPanelFold(localStorage.getItem('ct-panel-folded') === '1');

/* ── Persona view ────────────────────────────────────────────── */
async function loadPersonaList() {
  const sel  = document.getElementById('persona-select');
  const prev = sel.value;
  sel.innerHTML = '<option value="">— new persona —</option>';
  const list = await fetch('/api/personas').then(r => r.json()).catch(() => []);
  list.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = p.identity || p.id;
    sel.appendChild(opt);
  });
  if (prev) sel.value = prev;
}

function fillPersonaForm(p) {
  document.getElementById('persona-id').value = p.id || '';
  FIELDS.forEach(f => {
    const el = document.querySelector('[data-field="' + f + '"]');
    if (el) el.value = p[f] || '';
  });
  setAssertiveness(p.assertiveness || 'balanced');
  markPersonaClean();
}

function readPersonaForm() {
  const p = { id: document.getElementById('persona-id').value };
  FIELDS.forEach(f => {
    const el = document.querySelector('[data-field="' + f + '"]');
    p[f] = el ? el.value.trim() : '';
  });
  const activeBtn = document.querySelector('.btn-assertiveness.active-assertiveness');
  p.assertiveness = activeBtn ? activeBtn.dataset.value : 'balanced';
  return p;
}

function markPersonaDirty() {
  state.personaDirty = true;
  document.getElementById('persona-unsaved').classList.remove('hidden');
}
function markPersonaClean() {
  state.personaDirty = false;
  document.getElementById('persona-unsaved').classList.add('hidden');
}

document.querySelectorAll('[data-field]').forEach(el => {
  el.addEventListener('input', markPersonaDirty);
});

function setAssertiveness(key) {
  const valid = (key && ASSERTIVENESS[key]) ? key : 'balanced';
  document.querySelectorAll('.btn-assertiveness').forEach(btn => {
    btn.classList.toggle('active-assertiveness', btn.dataset.value === valid);
  });
  renderAssertIvenessPreview(valid);
}

function renderAssertIvenessPreview(key) {
  const dist = ASSERTIVENESS[key] || ASSERTIVENESS.balanced;
  const el = document.getElementById('assertiveness-preview');
  if (!el) return;
  el.innerHTML = Object.entries(dist).map(([lvl, pct]) =>
    '<div class="assertiveness-bar-row">'
    + '<span class="assertiveness-bar-label">' + escapeHtml(lvl.replace(/_/g,' ')) + '</span>'
    + '<div class="assertiveness-bar-track"><div class="assertiveness-bar-fill" style="width:' + pct + '%"></div></div>'
    + '<span class="assertiveness-bar-pct">' + pct + '%</span>'
    + '</div>'
  ).join('');
}

document.getElementById('assertiveness-selector').addEventListener('click', e => {
  const btn = e.target.closest('.btn-assertiveness');
  if (!btn) return;
  setAssertiveness(btn.dataset.value);
  markPersonaDirty();
});

document.getElementById('btn-load-persona').addEventListener('click', async () => {
  const id = document.getElementById('persona-select').value;
  if (!id) return;
  const p = await fetch('/api/personas/' + id).then(r => r.json());
  fillPersonaForm(p);
});

document.getElementById('btn-new-persona').addEventListener('click', () => {
  document.getElementById('persona-id').value = '';
  FIELDS.forEach(f => {
    const el = document.querySelector('[data-field="' + f + '"]');
    if (el) el.value = '';
  });
  document.getElementById('persona-select').value = '';
  setStatus(document.getElementById('persona-save-status'), '', '');
  setAssertiveness('balanced');
  markPersonaClean();
});

document.getElementById('btn-save-persona').addEventListener('click', async () => {
  const st = document.getElementById('persona-save-status');
  const p  = readPersonaForm();
  if (!p.identity && !p.core_desires) {
    setStatus(st, 'Fill in at least Identity and Core desires before saving.', 'err');
    return;
  }
  setStatus(st, 'Saving…', '');
  try {
    const res = await fetch('/api/personas', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p),
    }).then(r => r.json());
    document.getElementById('persona-id').value = res.id;
    await loadPersonaList();
    document.getElementById('persona-select').value = res.id;
    setStatus(st, 'Saved.', 'ok');
    markPersonaClean();
    showToast('Persona saved.');
  } catch (e) {
    setStatus(st, 'Save failed: ' + e.message, 'err');
  }
});

/* ── Import view ─────────────────────────────────────────────── */
document.getElementById('btn-import').addEventListener('click', async () => {
  const sheet    = document.getElementById('import-input').value.trim();
  const statusEl = document.getElementById('import-status');
  const btn      = document.getElementById('btn-import');

  if (!sheet) { setStatus(statusEl, 'Paste a character sheet first.', 'err'); return; }

  btn.textContent = 'Importing…';
  btn.disabled    = true;
  setStatus(statusEl, 'Processing your sheet — this takes about a minute…', '');
  document.getElementById('import-results').classList.add('hidden');

  try {
    const raw = await fetch('/api/import', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sheet }),
    });
    const res = await raw.json();
    if (!raw.ok) throw new Error(res.error || ('Server error ' + raw.status));

    state.importedFields = res.fields || {};

    const fieldsDiv = document.getElementById('import-fields');
    fieldsDiv.innerHTML = '';
    FIELDS.forEach(f => {
      const val   = (res.fields || {})[f];
      const block = document.createElement('div');
      block.className = 'import-field-row field-block';
      block.innerHTML =
        '<label>' + f.replace(/_/g, ' ') + '</label>'
        + '<textarea rows="2" data-import-field="' + f + '">'
        + escapeHtml(val || '') + '</textarea>';
      fieldsDiv.appendChild(block);
    });

    const gapsDiv = document.getElementById('import-gaps');
    gapsDiv.innerHTML = '';
    const gaps = Array.isArray(res.gaps) ? res.gaps : [];
    if (gaps.length === 0) {
      gapsDiv.innerHTML = '<p style="color:var(--muted);font-size:0.85rem;font-style:italic;">All fields were filled — no gaps found.</p>';
    } else {
      gaps.forEach(g => {
        const div = document.createElement('div');
        div.className = 'gap-item';
        div.innerHTML =
          '<div class="gap-field">' + escapeHtml(g.field || '') + '</div>'
          + '<div class="gap-missing">' + escapeHtml(g.missing || '') + '</div>';
        gapsDiv.appendChild(div);
      });
    }

    document.getElementById('import-results').classList.remove('hidden');
    setStatus(statusEl, 'Done — review the fields, then send to the Persona editor.', 'ok');
  } catch (e) {
    setStatus(statusEl, 'Import failed: ' + e.message, 'err');
  } finally {
    btn.textContent = 'Import';
    btn.disabled    = false;
  }
});

document.getElementById('btn-send-to-persona').addEventListener('click', () => {
  const fields = {};
  document.querySelectorAll('[data-import-field]').forEach(el => {
    fields[el.dataset.importField] = el.value.trim();
  });

  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelector('[data-view="persona"]').classList.add('active');
  document.getElementById('view-persona').classList.add('active');

  document.getElementById('persona-id').value = '';
  FIELDS.forEach(f => {
    const el = document.querySelector('[data-field="' + f + '"]');
    if (el) el.value = fields[f] || '';
  });

  loadPersonaList();
  setStatus(document.getElementById('persona-save-status'), 'Fields loaded from import — review and save.', 'ok');
  markPersonaDirty();
});

/* ── Settings view ───────────────────────────────────────────── */
let _settingsCfg       = null;
let _settingsModels    = [];
let _settingsDefaults  = null;
let _settingsRequired  = null;
let _settingsHelp      = null;
let _settingsProviders = {};

async function loadSettings() {
  try {
    const [cfgRes, provRes] = await Promise.all([
      fetch('/api/config').then(r => r.json()),
      fetch('/api/providers').then(r => r.json()).catch(() => ({})),
    ]);
    _settingsCfg       = cfgRes.config;
    _settingsModels    = cfgRes.available_models || [];
    _settingsDefaults  = cfgRes.defaults;
    _settingsRequired  = cfgRes.required_placeholders || {};
    _settingsHelp      = cfgRes.placeholder_help || {};
    _settingsProviders = provRes;
    await renderSettingsModelTemp();
    renderApiKeysSection();
    renderPromptEditor('appraisal', 'Appraisal prompt');
    renderPromptEditor('voice',     'Voice prompt');
    renderPromptEditor('import',    'Import prompt');
  } catch (e) {
    showToast('Could not load settings: ' + e.message);
  }
}

async function renderSettingsModelTemp() {
  const thoughtMdl = (_settingsCfg.thought_model && typeof _settingsCfg.thought_model === 'object')
    ? _settingsCfg.thought_model
    : { provider: 'ollama', model: _settingsCfg.thought_model || '' };
  const voiceMdl = (_settingsCfg.voice_model && typeof _settingsCfg.voice_model === 'object')
    ? _settingsCfg.voice_model
    : { provider: 'ollama', model: _settingsCfg.voice_model || '' };

  // Populate provider selects
  ['settings-thought-provider', 'settings-voice-provider'].forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    sel.innerHTML = '';
    Object.entries(_settingsProviders).forEach(([pid, info]) => {
      const opt = document.createElement('option');
      opt.value = pid;
      opt.textContent = info.label || pid;
      sel.appendChild(opt);
    });
    if (!sel.options.length) {
      const opt = document.createElement('option');
      opt.value = 'ollama'; opt.textContent = 'Ollama (local)';
      sel.appendChild(opt);
    }
  });

  const tProv = document.getElementById('settings-thought-provider');
  const vProv = document.getElementById('settings-voice-provider');
  const tMdl  = document.getElementById('settings-thought-model');
  const vMdl  = document.getElementById('settings-voice-model');

  if (tProv) tProv.value = thoughtMdl.provider || 'ollama';
  if (vProv) vProv.value = voiceMdl.provider   || 'ollama';
  if (tMdl)  tMdl.value  = thoughtMdl.model    || '';
  if (vMdl)  vMdl.value  = voiceMdl.model      || '';

  // Show/populate URL row for configurable-URL providers
  _updateProviderUrlRow('thought', thoughtMdl.provider || 'ollama', thoughtMdl.base_url || '');
  _updateProviderUrlRow('voice',   voiceMdl.provider   || 'ollama', voiceMdl.base_url   || '');

  // Fetch model lists for current providers in parallel
  await Promise.all([
    _loadModelDatalist('thought', thoughtMdl.provider || 'ollama'),
    _loadModelDatalist('voice',   voiceMdl.provider   || 'ollama'),
  ]);

  const taSlider = document.getElementById('settings-temp-appraisal');
  if (taSlider) {
    taSlider.value = _settingsCfg.temp_appraisal;
    document.getElementById('temp-appraisal-val').textContent = _settingsCfg.temp_appraisal;
  }
  const tvSlider = document.getElementById('settings-temp-voice');
  if (tvSlider) {
    tvSlider.value = _settingsCfg.temp_voice;
    document.getElementById('temp-voice-val').textContent = _settingsCfg.temp_voice;
  }
  const inspToggle = document.getElementById('settings-use-inspiration');
  if (inspToggle) inspToggle.checked = !!_settingsCfg.use_inspiration;
}

function _updateProviderUrlRow(role, providerId, currentBaseUrl) {
  const urlRow   = document.getElementById(role + '-url-row');
  const urlInput = document.getElementById('settings-' + role + '-base-url');
  if (!urlRow || !urlInput) return;
  const provInfo = _settingsProviders[providerId];
  if (provInfo && provInfo.configurable_url) {
    urlRow.style.display = '';
    urlInput.value = currentBaseUrl || provInfo.default_base_url || '';
  } else {
    urlRow.style.display = 'none';
    urlInput.value = '';
  }
}

async function _loadModelDatalist(role, providerId) {
  const dlId    = 'datalist-' + role + '-models';
  const datalist = document.getElementById(dlId);
  if (!datalist) return;
  datalist.innerHTML = '';
  try {
    let url = '/api/providers/' + encodeURIComponent(providerId) + '/models';
    const provInfo = _settingsProviders[providerId];
    if (provInfo && provInfo.configurable_url) {
      const urlInput = document.getElementById('settings-' + role + '-base-url');
      const baseUrl = (urlInput && urlInput.value.trim()) || provInfo.default_base_url || '';
      if (baseUrl) url += '?base_url=' + encodeURIComponent(baseUrl);
    }
    const res    = await fetch(url).then(r => r.json()).catch(() => ({}));
    const models = Array.isArray(res.models) ? res.models : [];
    models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m;
      datalist.appendChild(opt);
    });
  } catch (_) {}
}

function _keyRowHtml(pid, info, optional) {
  const statusHtml = info.key_set
    ? '<span class="key-status-badge key-set">set: ' + escapeHtml(info.masked) + '</span>'
    : '<span class="key-status-badge key-unset">not set</span>';
  const optBadge = optional ? ' <span class="key-optional">(optional)</span>' : '';
  return '<div class="key-row">'
    + '<div class="key-row-label">' + escapeHtml(info.label) + optBadge + ' ' + statusHtml + '</div>'
    + '<div class="key-row-controls">'
    + '<input type="password" class="key-input" id="key-input-' + escapeHtml(pid) + '" '
    + 'placeholder="paste key here" autocomplete="off" />'
    + '<button class="btn-small btn-save-key" data-provider="' + escapeHtml(pid) + '">Save</button>'
    + '<button class="btn-small btn-clear-key" data-provider="' + escapeHtml(pid) + '">Clear</button>'
    + '</div>'
    + '<span class="status-line key-msg" id="key-msg-' + escapeHtml(pid) + '"></span>'
    + '</div>';
}

function renderApiKeysSection() {
  const card = document.getElementById('settings-api-keys-card');
  if (!card) return;
  const cloudProviders    = Object.entries(_settingsProviders).filter(([, info]) => info.needs_key);
  const optionalProviders = Object.entries(_settingsProviders).filter(([, info]) => info.optional_key && !info.needs_key);
  if (!cloudProviders.length && !optionalProviders.length) {
    card.innerHTML = '<h3 class="settings-card-title">API Keys</h3>'
      + '<p class="settings-hint">No providers with keys available.</p>';
    return;
  }
  let html = '<h3 class="settings-card-title">API Keys</h3>';
  cloudProviders.forEach(([pid, info]) => {
    html += _keyRowHtml(pid, info, false);
  });
  if (optionalProviders.length) {
    html += '<p class="settings-hint" style="margin-top:1rem;margin-bottom:0.5rem">'
      + 'Local servers — only needed if your server requires authentication:</p>';
    optionalProviders.forEach(([pid, info]) => {
      html += _keyRowHtml(pid, info, true);
    });
  }
  card.innerHTML = html;
  card.querySelectorAll('.btn-save-key').forEach(btn => {
    btn.addEventListener('click', () => _saveApiKey(btn.dataset.provider));
  });
  card.querySelectorAll('.btn-clear-key').forEach(btn => {
    btn.addEventListener('click', () => _clearApiKey(btn.dataset.provider));
  });
}

async function _saveApiKey(providerId) {
  const input   = document.getElementById('key-input-' + providerId);
  const msgEl   = document.getElementById('key-msg-' + providerId);
  const keyVal  = input ? input.value : '';
  if (!keyVal.trim()) { setStatus(msgEl, 'Paste a key first.', 'err'); return; }
  setStatus(msgEl, 'Saving…', '');
  try {
    const res = await fetch('/api/keys', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ provider: providerId, key: keyVal }),
    }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    _settingsProviders = res;
    if (input) input.value = '';  // never display the key
    renderApiKeysSection();
    showToast('API key saved.');
  } catch (e) {
    if (msgEl) setStatus(msgEl, 'Failed: ' + e.message, 'err');
  }
}

async function _clearApiKey(providerId) {
  const msgEl = document.getElementById('key-msg-' + providerId);
  setStatus(msgEl, 'Clearing…', '');
  try {
    const res = await fetch('/api/keys', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ provider: providerId, key: '' }),
    }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    _settingsProviders = res;
    renderApiKeysSection();
    showToast('API key cleared.');
  } catch (e) {
    if (msgEl) setStatus(msgEl, 'Failed: ' + e.message, 'err');
  }
}

function renderPromptEditor(name, title) {
  const el = document.getElementById('prompt-' + name + '-editor');
  const required = (_settingsRequired[name] || []);
  const currentText = (_settingsCfg.prompts || {})[name] || '';

  const varRows = required.map(v =>
    '<tr><td><code class="var-pill">{' + escapeHtml(v) + '}</code></td>'
    + '<td class="var-help">' + escapeHtml((_settingsHelp[v] || '')) + '</td></tr>'
  ).join('');

  el.innerHTML =
    '<h3 class="settings-card-title">' + escapeHtml(title) + '</h3>'
    + '<div class="prompt-vars">'
    + '<div class="prompt-vars-label">Required — these placeholders must stay in the prompt:</div>'
    + '<table class="prompt-var-table">' + varRows + '</table>'
    + '</div>'
    + '<textarea class="prompt-textarea" id="prompt-textarea-' + name
    + '" spellcheck="false"></textarea>'
    + '<div class="prompt-actions">'
    + '<button class="btn-primary btn-save-prompt" data-name="' + name + '">Save prompt</button>'
    + '<button class="btn-small btn-reset-prompt" data-name="' + name + '">Reset to default</button>'
    + '<span class="prompt-status status-line" id="prompt-status-' + name + '"></span>'
    + '</div>';

  document.getElementById('prompt-textarea-' + name).value = currentText;
  el.querySelector('.btn-save-prompt').addEventListener('click',  () => savePrompt(name));
  el.querySelector('.btn-reset-prompt').addEventListener('click', () => resetPrompt(name));
}

async function savePrompt(name) {
  const textarea = document.getElementById('prompt-textarea-' + name);
  const statusEl = document.getElementById('prompt-status-' + name);
  const text = textarea.value;
  const required = _settingsRequired[name] || [];
  const missing = required.filter(v => !text.includes('{' + v + '}'));
  if (missing.length) {
    setStatus(statusEl,
      'Missing: ' + missing.map(v => '{' + v + '}').join(', ')
      + ' — add them back before saving.', 'err');
    return;
  }
  setStatus(statusEl, 'Saving…', '');
  try {
    const res = await fetch('/api/config', {
      method:  'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompts: { [name]: text } }),
    }).then(r => r.json());
    if (res.error === 'missing_placeholders') {
      const details = res.details || {};
      const msgs = Object.values(details).flat().map(v => '{' + v + '}').join(', ');
      setStatus(statusEl, 'Server rejected — missing: ' + msgs, 'err');
    } else {
      _settingsCfg = res.config;
      setStatus(statusEl, 'Saved — live on next message.', 'ok');
      showToast('Prompt saved.');
    }
  } catch (e) {
    setStatus(statusEl, 'Save failed: ' + e.message, 'err');
  }
}

async function resetPrompt(name) {
  const statusEl = document.getElementById('prompt-status-' + name);
  setStatus(statusEl, 'Resetting…', '');
  try {
    const res = await fetch('/api/config/reset', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: name }),
    }).then(r => r.json());
    _settingsCfg = res.config;
    document.getElementById('prompt-textarea-' + name).value = res.config.prompts[name];
    setStatus(statusEl, 'Reset to default.', 'ok');
    showToast('Reset to default.');
  } catch (e) {
    setStatus(statusEl, 'Reset failed: ' + e.message, 'err');
  }
}

document.getElementById('settings-temp-appraisal').addEventListener('input', function () {
  document.getElementById('temp-appraisal-val').textContent = parseFloat(this.value).toFixed(2);
});
document.getElementById('settings-temp-voice').addEventListener('input', function () {
  document.getElementById('temp-voice-val').textContent = parseFloat(this.value).toFixed(2);
});

document.getElementById('btn-save-model-temps').addEventListener('click', async () => {
  const statusEl = document.getElementById('model-temp-status');
  const thoughtProvider = (document.getElementById('settings-thought-provider') || {}).value || 'ollama';
  const thought_model = {
    provider: thoughtProvider,
    model:    ((document.getElementById('settings-thought-model') || {}).value || '').trim(),
  };
  const tProvInfo = _settingsProviders[thoughtProvider];
  if (tProvInfo && tProvInfo.configurable_url) {
    const urlEl = document.getElementById('settings-thought-base-url');
    if (urlEl) thought_model.base_url = urlEl.value.trim();
  }
  const voiceProvider = (document.getElementById('settings-voice-provider') || {}).value || 'ollama';
  const voice_model = {
    provider: voiceProvider,
    model:    ((document.getElementById('settings-voice-model') || {}).value || '').trim(),
  };
  const vProvInfo = _settingsProviders[voiceProvider];
  if (vProvInfo && vProvInfo.configurable_url) {
    const urlEl = document.getElementById('settings-voice-base-url');
    if (urlEl) voice_model.base_url = urlEl.value.trim();
  }
  const temp_appraisal  = parseFloat(document.getElementById('settings-temp-appraisal').value);
  const temp_voice      = parseFloat(document.getElementById('settings-temp-voice').value);
  const use_inspiration = !!(document.getElementById('settings-use-inspiration') || {}).checked;

  if (!thought_model.model) { setStatus(statusEl, 'Enter a model name for Thought.', 'err'); return; }
  if (!voice_model.model)   { setStatus(statusEl, 'Enter a model name for Voice.', 'err'); return; }

  setStatus(statusEl, 'Saving…', '');
  try {
    const res = await fetch('/api/config', {
      method:  'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thought_model, voice_model, temp_appraisal, temp_voice, use_inspiration }),
    }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    _settingsCfg = res.config;
    setStatus(statusEl, 'Saved — live on next message.', 'ok');
    showToast('Models and temperatures saved.');
  } catch (e) {
    setStatus(statusEl, 'Save failed: ' + e.message, 'err');
  }
});

// When provider changes, update URL row then fetch that provider's model list
document.getElementById('settings-thought-provider')?.addEventListener('change', function () {
  _updateProviderUrlRow('thought', this.value, '');
  _loadModelDatalist('thought', this.value);
  const inp = document.getElementById('settings-thought-model');
  if (inp) inp.value = '';
});
document.getElementById('settings-voice-provider')?.addEventListener('change', function () {
  _updateProviderUrlRow('voice', this.value, '');
  _loadModelDatalist('voice', this.value);
  const inp = document.getElementById('settings-voice-model');
  if (inp) inp.value = '';
});

/* ── Init ────────────────────────────────────────────────────── */
setAssertiveness('balanced');
loadChatPersonaList();
