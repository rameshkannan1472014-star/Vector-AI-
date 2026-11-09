/* ================================================================== */
/* Vector AI front end                                                 */
/* ================================================================== */
const $ = id => document.getElementById(id);

/* Always send the session cookie so the server can see who is signed in. */
async function credFetch(url, opts){
  const o = (opts || {});
  return fetch(url, Object.assign({ credentials: 'include' }, o));
}


const input     = $('input');
const sendBtn   = $('send');
const messagesEl= $('messages');
const hero      = $('hero');
const scroller  = $('scroller');
const sidebar   = $('sidebar');
const threadsEl = $('threads');
const toastEl   = $('toast');

const LS = {
  chats: 'vector.chats.v1',
  mem:   'vector.memory.v1',
  proj:  'vector.projects.v1',
  notes: 'vector.notes.v1'
};

const STYLE = 'Reply in short, friendly plain text with emojis. ' +
              'Never use markdown: no asterisks, no #, no code blocks, no bullet dashes. ' +
              'Explain it simply, like you are talking to a 12 year old. ' +
              'Do not start every reply with the same greeting. Greet only on the very first message of a chat, ' +
              'and change the wording each time. On later messages go straight to the answer.';

let busy = false;
let mode = 'chat';
let provider = 'gemini';
let providerList = [];

let turns = [];
let activeId = null;
let chats = [];

let memory = { name: 'Ramesh', age: '12', about: '', preferences: '' };
let notes = [];
let projects = [];
let activeProject = null;
let attachments = [];

let me = null;
let authProviders = { github: false, google: false };

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */
function toast(msg){
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  clearTimeout(toastEl._t);
  toastEl._t = setTimeout(() => toastEl.classList.remove('show'), 2400);
}
function uid(){ return Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }
function escapeHtml(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function escapeJs(s){
  if (s == null) return '';
  s = String(s);
  s = s.replace(new RegExp(String.fromCharCode(92,92), 'g'), String.fromCharCode(92,92,92,92));
  s = s.replace(new RegExp(String.fromCharCode(39), 'g'), String.fromCharCode(92,39));
  return s;
}
function escapeAttr(s){
  return escapeHtml(s).replace(/"/g,'&quot;');
}
function readLS(key, fallback){
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; }
  catch (e) { return fallback; }
}
function writeLS(key, value){
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
}

function plainRender(text){
  let t = String(text == null ? '' : text);
  t = t.replace(/```[a-zA-Z0-9+#-]*\n?/g, '').replace(/```/g, '');
  t = t.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*\n]+)\*/g, '$1');
  t = t.replace(/__([^_]+)__/g, '$1');
  t = t.replace(/`([^`]+)`/g, '$1');
  t = t.replace(/^#{1,6}\s*/gm, '');
  t = t.replace(/^\s*[-*+]\s+/gm, '• ');
  t = t.replace(/\n{3,}/g, '\n\n').trim();
  return escapeHtml(t).replace(/\n/g, '<br>');
}

/* ------------------------------------------------------------------ */
/* composer                                                            */
/* ------------------------------------------------------------------ */
function autogrow(){
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 220) + 'px';
  sendBtn.classList.toggle('ready', input.value.trim().length > 0 && !busy);
  sendBtn.disabled = !input.value.trim() || busy;
}

input.addEventListener('input', autogrow);
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); send(); }
});

function useChip(el){
  input.value = el.textContent;
  autogrow();
  input.focus();
}

function toggleSidebar(){
  sidebar.classList.toggle('hidden');
  syncSide();
}

/* ------------------------------------------------------------------ */
/* modes                                                               */
/* ------------------------------------------------------------------ */
const MODES = ['chat','code','debug','review','diagram','image'];

function labelFor(m){
  return { chat:'Agent', code:'Code', debug:'Debug', review:'Review', diagram:'Diagram', image:'Image' }[m] || 'Agent';
}
function setGreeting(m){
  const greetings = {
    chat:    ['Hey Ramesh,', 'how are things going?', 'Ask anything, paste an error, or drop a file. Vector works through code, debugging and engineering problems with you.'],
    code:    ['Hey Ramesh,', 'what should we build today?', 'Describe what you want and Vector will help you build it step by step.'],
    debug:   ['Hey Ramesh,', 'what looks broken?', 'Paste the error or the stack trace and we will work out why it happens.'],
    review:  ['Hey Ramesh,', 'what should I look at?', 'Paste your code and Vector will look for bugs, slow parts and easy wins.'],
    diagram: ['Hey Ramesh,', 'what should we map out?', 'Describe the system and Vector will sketch how the pieces fit together.'],
    image:   ['Hey Ramesh,', 'what picture should I make?', 'Describe a picture and Vector will generate it for you.']
  };
  const copy = greetings[m] || ['Hey Ramesh,', 'what can I help with?', 'Ask Vector anything, and I will help you out.'];

  $('heroTitle').innerHTML = escapeHtml(copy[0]) + '<span>' + escapeHtml(copy[1]) + '</span>';
  $('heroSub').textContent = copy[2];

  const ph = {
    chat:'Ask Vector anything', code:'Describe what to build', debug:'Paste the error or stack trace',
    review:'Paste the code to review', diagram:'Describe the system', image:'Describe the picture'
  }[m];
  input.placeholder = ph || 'Ask Vector anything';
}
function cycleMode(){
  const at = MODES.indexOf(mode);
  mode = MODES[(at + 1) % MODES.length];
  $('modeLabel').textContent = labelFor(mode);
  setGreeting(mode);
  input.focus();
}

/* ------------------------------------------------------------------ */
/* chats                                                               */
/* ------------------------------------------------------------------ */
function loadState(){
  chats    = readLS(LS.chats, []);
  notes    = readLS(LS.notes, []);
  projects = readLS(LS.proj, []);
  const saved = readLS(LS.mem, null);
  if (saved && typeof saved === 'object') memory = Object.assign({}, memory, saved);
  paintMemoryBadge();
  renderChats();
}

function persistChats(){
  writeLS(LS.chats, chats.slice(0, 60));
}

function activeChat(){ return chats.find(c => c.id === activeId) || null; }

function ensureChat(firstText){
  const existing = activeChat();
  if (existing) return existing;
  const chat = {
    id: uid(),
    title: (firstText || 'New chat').slice(0, 52),
    messages: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
    projectId: activeProject ? activeProject.id : null
  };
  chats.unshift(chat);
  activeId = chat.id;
  return chat;
}

function syncChat(){
  const chat = activeChat();
  if (!chat) return;
  chat.messages = turns.slice();
  chat.updatedAt = Date.now();
  if (!chat.title || chat.title === 'New chat') {
    const firstUser = turns.find(t => t.role === 'user');
    if (firstUser) chat.title = String(firstUser.content).slice(0, 52);
  }
  persistChats();
  renderChats();
}

function newChat(){
  activeId = null;
  turns = [];
  messagesEl.innerHTML = '';
  messagesEl.style.display = 'none';
  hero.style.display = 'flex';
  const chips = $('suggestions');
  if (chips) chips.style.display = 'flex';
  input.value = '';
  autogrow();
  scroller.scrollTop = 0;
  closeDrawer();
  renderChats();
}

function goHome(){
  newChat();
  closeSidebarOnMobile();
}

function openChat(id){
  const chat = chats.find(c => c.id === id);
  if (!chat) return;
  activeId = chat.id;
  turns = (chat.messages || []).slice();
  if (chat.projectId){
    const p = projects.find(x => x.id === chat.projectId);
    if (p) activeProject = p;
  }
  paintProject();
  paintThread();
  renderChats();
  closeDrawer();
  closeSidebarOnMobile();
}

function groupFor(ts){
  const now = new Date();
  const d = new Date(ts || Date.now());
  const day = 86400000;
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (d.getTime() >= startToday) return 'Today';
  if (d.getTime() >= startToday - day) return 'Yesterday';
  if (d.getTime() >= startToday - 7 * day) return 'Previous 7 days';
  return 'Older';
}

function renderChats(){
  const q = ($('searchInput').value || '').trim().toLowerCase();
  const list = chats.filter(c => {
    if (!q) return true;
    const hay = (c.title + ' ' + (c.messages || []).map(m => m.content).join(' ')).toLowerCase();
    return hay.indexOf(q) > -1;
  });

  threadsEl.innerHTML = '';

  if (!list.length){
    const note = document.createElement('div');
    note.className = 'empty-note';
    note.textContent = q ? 'No chats match that.' : 'Your chats appear here. Tap New chat to start one.';
    threadsEl.appendChild(note);
    return;
  }

  const order = ['Today','Yesterday','Previous 7 days','Older'];
  const groups = {};
  list.forEach(c => {
    const key = groupFor(c.updatedAt || c.createdAt);
    (groups[key] = groups[key] || []).push(c);
  });

  order.forEach(name => {
    if (!groups[name]) return;
    const label = document.createElement('div');
    label.className = 'group-label';
    label.textContent = name;
    threadsEl.appendChild(label);

    groups[name].forEach(c => {
      const b = document.createElement('button');
      b.className = 'thread' + (c.id === activeId ? ' active' : '');
      b.textContent = c.title || 'New chat';
      b.onclick = () => openChat(c.id);
      threadsEl.appendChild(b);
    });
  });
}

function toggleSearch(){
  const box = $('searchBox');
  box.classList.toggle('open');
  if (box.classList.contains('open')) $('searchInput').focus();
  else { $('searchInput').value = ''; renderChats(); }
}

/* ------------------------------------------------------------------ */
/* thread painting                                                     */
/* ------------------------------------------------------------------ */
function addUser(text){
  const el = document.createElement('div');
  el.className = 'msg user';
  el.innerHTML =
    '<div class="head user-head"><div class="ai-mark" style="background:#fff;color:#000">U</div><div class="ai-name" style="color:var(--text-dim)">You</div></div>' +
    '<div class="body user-body"></div>';
  messagesEl.appendChild(el);
  el.querySelector('.user-body').innerHTML = plainRender(text);
  scrollBottom();
}

function addAI(){
  const el = document.createElement('div');
  el.className = 'msg ai';
  el.innerHTML =
    '<div class="head"><div class="ai-mark">V</div><div class="ai-name">Vector</div></div>' +
    '<div class="body"><span class="caret"></span></div>';
  messagesEl.appendChild(el);
  scrollBottom();
  return el.querySelector('.body');
}

function scrollBottom(){
  requestAnimationFrame(() => { scroller.scrollTop = scroller.scrollHeight; });
}

/* ------------------------------------------------------------------ */
/* buttons at the end of every reply                                   */
/* ------------------------------------------------------------------ */
const FOLLOW_UPS = [
  'Explain that simpler 🙏',
  'Give me a short example 💡',
  'What should I do next? 🚀'
];

function answerText(index){
  const found = index >= 0
    ? turns[index]
    : turns.slice().reverse().find(m => m.role === 'assistant');
  return found && found.role === 'assistant' ? String(found.content || '') : '';
}

function copyAnswer(index){
  const text = answerText(index);
  if (!text){ toast('Nothing to copy yet'); return; }
  if (navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(text)
      .then(() => toast('Answer copied ✅'))
      .catch(() => toast('Copying is blocked in this browser'));
  } else {
    toast('Copying is not available in this browser');
  }
}

function saveAnswer(index){
  const text = answerText(index);
  if (!text){ toast('Nothing to save yet'); return; }
  notes.unshift('Vector said: ' + text.replace(/\s+/g, ' ').slice(0, 130));
  notes = notes.slice(0, 20);
function rateAnswer(index, good){
  const store = readLS('vector.feedback.v1', []);
  store.unshift({ at: Date.now(), good: !!good, text: answerText(index).slice(0, 400) });
  writeLS('vector.feedback.v1', store.slice(0, 200));
  toast(good ? 'Noted — more like this 👍' : 'Noted — I will try better 👎');
}

function readAloud(index){
  const text = answerText(index);
  if (!text){ toast('Nothing to read yet'); return; }
  if (!('speechSynthesis' in window)){ toast('This browser cannot speak text'); return; }
  try {
    if (speechSynthesis.speaking){ speechSynthesis.cancel(); toast('Stopped reading'); return; }
    const clean = String(text).replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/gu, '').replace(/[•*#`]/g, '').slice(0, 2000);
    const u = new SpeechSynthesisUtterance(clean);
    u.rate = 1.02;
    u.onend = () => toast('Finished reading');
    speechSynthesis.speak(u);
    toast('Reading aloud 🔊');
  } catch (e) { toast('Reading aloud is blocked in this browser'); }
}

function shareAnswer(index){
  const text = answerText(index);
  if (!text){ toast('Nothing to share yet'); return; }
  const full = text + '\n— from Vector AI';
  if (navigator.share){ navigator.share({ title: 'Vector AI', text: full }).catch(() => copyText(full)); return; }
  copyText(full);
}

function branchChat(index){
  const upto = index >= 0 ? turns.slice(0, index + 1) : turns.slice();
  if (!upto.length){ toast('Nothing to branch yet'); return; }
  const source = activeChat();
  const firstUser = upto.find(t => t.role === 'user');
  const chat = {
    id: uid(),
    title: 'Branch · ' + String((firstUser && firstUser.content) || 'chat').slice(0, 44),
    messages: upto.map(m => ({ role: m.role, content: m.content })),
    createdAt: Date.now(),
    updatedAt: Date.now(),
    projectId: source ? source.projectId : null
  };
  chats.unshift(chat);
  persistChats();
  activeId = chat.id;
  turns = chat.messages.slice();
  paintThread();
  renderChats();
  closeDrawer();
  closeSidebarOnMobile();
  toast('Branched into a new chat 🌿');
}

function copyText(text){
  if (navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(text)
      .then(() => toast('Copied — paste it anywhere ✅'))
      .catch(() => toast('Copying is blocked in this browser'));
  } else { toast('Copying is not available in this browser'); }
}

}

function sendText(text){
  input.value = String(text == null ? '' : text);
  autogrow();
  send();
}

function retryAt(index){
  if (busy){ toast('One moment, still answering…'); return; }
  let at = index >= 0 ? index - 1 : turns.length - 1;
  while (at >= 0 && turns[at].role !== 'user') at--;
  if (at < 0){ toast('Nothing to try again'); return; }
  const said = String(turns[at].content || '');
  turns.splice(at, turns.length - at);
  paintThread();
  syncChat();
  sendText(said);
}

const ICONS = {
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 5.5H6.5A1.5 1.5 0 0 0 5 7v8.5"/></svg>',
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M7 20V9l4-6 1.7.9a3 3 0 0 1 1.5 3.4L13.5 9H19a2 2 0 0 1 2 2.4l-1.2 6.6A2 2 0 0 1 17.8 20H7Z"/><path d="M7 9.5H4V20h3"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4v11l4 6 1.7-.9a3 3 0 0 0 1.5-3.4L13.5 15H19a2 2 0 0 0 2-2.4l-1.2-6.6A2 2 0 0 0 17.8 4H7Z"/><path d="M7 14.5H4V4h3"/></svg>',
  speak: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 9.5v5h3l5 4V5.5l-5 4H4Z"/><path d="M16.5 9a4.2 4.2 0 0 1 0 6"/><path d="M19 6.5a7.8 7.8 0 0 1 0 11"/></svg>',
  share: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="8" r="1.6" fill="currentColor"/><circle cx="18" cy="12.5" r="1.6" fill="currentColor"/><path d="M2 12s3-4 8-4 8 4 8 4-3 4-8 4-8-4-8-4Z"/></svg>',
  retry: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v4.5h-4.5"/></svg>',
  branch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="5" r="2.2"/><circle cx="7" cy="19" r="2.2"/><circle cx="17" cy="9" r="2.2"/><path d="M7 7.2v9.6"/><path d="M17 11.2c0 4.2-4 3.4-6.5 4.6"/></svg>'
};

function attachActions(msgEl, index){
  if (!msgEl) return;
  const old = msgEl.querySelector('.msg-actions');
  if (old) old.remove();
  const oldFollow = msgEl.querySelector('.followups');
  if (oldFollow) oldFollow.remove();

  const row = document.createElement('div');
  row.className = 'msg-actions';

  const spec = [
    { icon: 'copy', title: 'Copy answer', run: () => copyAnswer(index) },
    { icon: 'up', title: 'Good answer', run: () => rateAnswer(index, true) },
    { icon: 'down', title: 'Bad answer', run: () => rateAnswer(index, false) },
    { icon: 'speak', title: 'Read aloud', run: () => readAloud(index) },
    { icon: 'share', title: 'Share answer', run: () => shareAnswer(index) },
    { sep: true },
    { icon: 'retry', title: 'Try again', run: () => retryAt(index) },
    { icon: 'branch', title: 'Branch into a new chat', run: () => branchChat(index) }
  ];
  spec.forEach(a => {
    if (a.sep){
      const w = document.createElement('span'); w.className = 'sep'; row.appendChild(w); return;
    }
    const b = document.createElement('button');
    b.className = 'act';
    b.type = 'button';
    b.setAttribute('aria-label', a.title);
    b.title = a.title;
    b.innerHTML = ICONS[a.icon];
    b.onclick = a.run;
    row.appendChild(b);
  });
  msgEl.appendChild(row);

  if (index !== turns.length - 1 && index >= 0) return;

  const follow = document.createElement('div');
  follow.className = 'followups';
  FOLLOW_UPS.forEach(q => {
    const b = document.createElement('button');
    b.className = 'chip';
    b.textContent = q;
    b.onclick = () => sendText(q);
    follow.appendChild(b);
  });
  msgEl.appendChild(follow);
}

function paintThread(){
  messagesEl.innerHTML = '';
  const chips = $('suggestions');

  if (!turns.length){
    messagesEl.style.display = 'none';
    hero.style.display = 'flex';
    if (chips) chips.style.display = 'flex';
    return;
  }

  hero.style.display = 'none';
  messagesEl.style.display = 'block';
  if (chips) chips.style.display = 'none';

  turns.forEach((m, i) => {
    if (m.role === 'user') addUser(m.content);
    else {
      const body = addAI();
      body.innerHTML = plainRender(m.content);
      attachActions(body.parentElement, i);
    }
  });

  scroller.scrollTop = scroller.scrollHeight;
}

/* ------------------------------------------------------------------ */
/* providers                                                           */
/* ------------------------------------------------------------------ */
const providerTag  = $('providerTag');
const providerName = $('providerName');

async function loadProviders(){
  try {
    const res = await credFetch('/api/providers');
    if (!res.ok) return;
    const data = await res.json();
    providerList = Array.isArray(data.providers) ? data.providers : [];
    const usable = providerList.filter(p => p.configured);
    const pick = usable[0] || providerList[0];
    if (pick){ provider = pick.id; providerName.textContent = pick.label; }
  } catch (e) {
    providerName.textContent = 'Gemini';
  }
}

if (providerTag){
  providerTag.addEventListener('click', () => {
    const usable = providerList.filter(p => p.configured);
    if (usable.length < 2){ toast('This is the model your server is using'); return; }
    const at = usable.findIndex(p => p.id === provider);
    const next = usable[(at + 1) % usable.length];
    provider = next.id;
    providerName.textContent = next.label;
    toast('Using ' + next.label);
  });
}

/* ------------------------------------------------------------------ */
/* memory                                                              */
/* ------------------------------------------------------------------ */
function memoryPayload(){
  const about = [memory.about].concat(notes).filter(Boolean).join(' | ');
  const out = {};
  if (memory.name) out.name = memory.name;
  if (memory.age) out.age = memory.age;
  if (about) out.about = about.slice(0, 2000);
  if (memory.preferences) out.preferences = memory.preferences;
  return out;
}

function paintMemoryBadge(){
  const bits = [];
  if (memory.name) bits.push(memory.name);
  $('memCount').textContent = notes.length ? '+' + notes.length : (bits.length ? 'saved' : '');
}

function openMemory(){
  closeAcctMenu();
  $('drawerTitle').textContent = 'Memory';
  $('drawerBody').innerHTML = memoryTemplate();
  openDrawer();
}

function memoryTemplate(){
  const noteList = notes.length
    ? notes.map((n, i) =>
        '<div class="mem-note"><div>' + escapeHtml(n) + '</div>' +
        '<button onclick="dropNote(' + i + ')" title="Remove">×</button></div>').join('')
    : '<div class="empty-note" style="padding-left:0">Nothing saved from chats yet.</div>';

  return '' +
    '<p class="hint" style="margin:0 0 14px">This is what Vector knows about you. It travels with every message.</p>' +
    '<div class="field"><label>Your name</label><input id="mName" type="text" value="' + escapeAttr(memory.name) + '"></div>' +
    '<div class="field"><label>Your age</label><input id="mAge" type="text" value="' + escapeAttr(memory.age) + '"></div>' +
    '<div class="field"><label>About you</label><textarea id="mAbout" placeholder="What should Vector know? e.g. I am building a game.">' + escapeHtml(memory.about) + '</textarea></div>' +
    '<div class="field"><label>Things you like</label><textarea id="mPrefs" placeholder="e.g. short answers, fun examples">' + escapeHtml(memory.preferences) + '</textarea></div>' +
    '<button class="btn block" onclick="saveMemory()">Save memory</button>' +
    '<div style="height:10px"></div>' +
    '<div style="display:flex;gap:8px">' +
      '<button class="btn ghost" style="flex:1" onclick="rememberChat()">Remember this chat</button>' +
      '<button class="btn ghost" onclick="resetMemory()">Reset</button>' +
    '</div>' +
    '<div class="hint" style="margin-bottom:8px">Saved from chats</div>' + noteList;
}

function saveMemory(){
  memory.name        = ($('mName').value || '').trim();
  memory.age         = ($('mAge').value || '').trim();
  memory.about       = ($('mAbout').value || '').trim();
  memory.preferences = ($('mPrefs').value || '').trim();
  writeLS(LS.mem, memory);
  paintMemoryBadge();
  paintAccount();
  toast('Memory saved');
}

function resetMemory(){
  memory = { name: 'Ramesh', age: '12', about: '', preferences: '' };
  notes = [];
  writeLS(LS.mem, memory);
  writeLS(LS.notes, notes);
  paintMemoryBadge();
  paintAccount();
  openMemory();
  toast('Memory cleared');
}

function rememberChat(){
  if (!turns.length){ toast('Open a chat first'); return; }
  const said = turns.filter(m => m.role === 'user').slice(-1)[0];
  const reply = turns.filter(m => m.role === 'assistant').slice(-1)[0];
  const line = (said ? 'We talked about ' + String(said.content).slice(0, 110) : 'A chat') +
               (reply ? ' — Vector said ' + String(reply.content).replace(/\s+/g, ' ').slice(0, 90) : '');
  notes.unshift(line);
  notes = notes.slice(0, 20);
  writeLS(LS.notes, notes);
  paintMemoryBadge();
  openMemory();
  toast('Saved to memory');
}

function dropNote(i){
  notes.splice(i, 1);
  writeLS(LS.notes, notes);
  paintMemoryBadge();
  openMemory();
}

/* ------------------------------------------------------------------ */
/* projects                                                            */
/* ------------------------------------------------------------------ */
async function openProjects(){
  closeAcctMenu();
  $('drawerTitle').textContent = 'Projects';
  $('drawerBody').innerHTML = '<div class="empty-note">Loading your projects…</div>';
  openDrawer();
  await loadProjects();
  renderProjects();
}

async function loadProjects(){
  try {
    const res = await credFetch('/api/projects');
    if (!res.ok) throw new Error('bad status');
    const data = await res.json();
    if (Array.isArray(data.projects)){
      projects = data.projects.map(p => ({
        id: p.id, name: p.name, language: p.language,
        progress: p.progress, description: p.description || ''
      }));
      writeLS(LS.proj, projects);
    }
  } catch (e) {}
  paintProject();
}

function renderProjects(){
  const body = $('drawerBody');
  const list = projects.length
    ? projects.map(p =>
        '<button class="proj' + (activeProject && activeProject.id === p.id ? ' active' : '') +
        '" onclick="selectProject(\'' + p.id + '\')">' +
        '<b>' + escapeHtml(p.name) + '</b>' +
        '<small>' + escapeHtml(p.language || 'text') +
        (p.progress ? ' · ' + p.progress + '%' : '') + '</small></button>').join('')
    : '<div class="empty-note" style="padding-left:0">No projects yet. Make one below and Vector will work inside it.</div>';

  body.innerHTML = '' +
    '<div class="hint" style="margin:0 0 12px">Pick a project and Vector keeps its details in mind while you chat.</div>' +
    list +
    '<div class="field" style="margin-top:16px"><label>New project</label>' +
    '<input id="pName" type="text" placeholder="Project name"></div>' +
    '<div class="field"><textarea id="pDesc" placeholder="What is it about? (optional)"></textarea></div>' +
    '<button class="btn block" onclick="createProject()">Create project</button>' +
    (activeProject ? '<div style="height:10px"></div><button class="btn ghost block" onclick="clearProject();renderProjects()">Stop working in a project</button>' : '');
}

async function createProject(){
  const name = ($('pName').value || '').trim();
  const description = ($('pDesc').value || '').trim();
  if (!name){ toast('Give the project a name'); return; }

  try {
    const res = await credFetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, language: 'text', description: description, code: '' })
    });
    if (!res.ok) throw new Error('failed');
    await loadProjects();
    const made = projects.find(p => p.name === name) || projects[0];
    if (made) activeProject = made;
    toast('Project created');
  } catch (e) {
    const local = { id: uid(), name: name, language: 'text', progress: 0, description: description };
    projects.unshift(local);
    writeLS(LS.proj, projects);
    activeProject = local;
    toast('Saved on this device');
  }
  paintProject();
  renderProjects();
}

function selectProject(id){
  const p = projects.find(x => x.id === id);
  if (!p) return;
  activeProject = p;
  paintProject();
  renderProjects();
  toast('Working in ' + p.name);
}

function clearProject(){
  activeProject = null;
  paintProject();
}

function paintProject(){
  const bar = $('projectBar');
  const count = $('projCount');
  if (count) count.textContent = projects.length ? String(projects.length) : '';
  if (activeProject){
    $('projectName').textContent = activeProject.name;
    bar.classList.add('open');
  } else {
    bar.classList.remove('open');
  }
}

/* ------------------------------------------------------------------ */
/* attachments                                                         */
/* ------------------------------------------------------------------ */
function pickFile(){ $('fileInput').click(); }

function onFiles(e){
  const files = Array.from(e.target.files || []);
  files.forEach(file => {
    if (file.type.indexOf('image/') === 0){
      const reader = new FileReader();
      reader.onload = () => {
        attachments.push({
          name: file.name, kind: 'image', mime_type: file.type,
          data: String(reader.result).split(',')[1]
        });
        paintAttachments();
      };
      reader.readAsDataURL(file);
    } else if (file.size <= 400000){
      const reader = new FileReader();
      reader.onload = () => {
        attachments.push({ name: file.name, kind: 'text', text: String(reader.result).slice(0, 12000) });
        paintAttachments();
      };
      reader.readAsText(file);
    } else {
      toast('That file is too big (400 KB max)');
    }
  });
  e.target.value = '';
}

function paintAttachments(){
  const strip = $('attachStrip');
  strip.innerHTML = attachments.map((a, i) =>
    '<div class="attach-chip"><span>' + (a.kind === 'image' ? '🖼️ ' : '📄 ') + escapeHtml(a.name) + '</span>' +
    '<button onclick="removeAttachment(' + i + ')">×</button></div>').join('');
  strip.classList.toggle('open', attachments.length > 0);
}

function removeAttachment(i){
  attachments.splice(i, 1);
  paintAttachments();
}

function contextPayload(){
  const parts = [];
  if (activeProject){
    parts.push('Project: ' + activeProject.name +
      (activeProject.description ? '\n' + activeProject.description : ''));
  }
  if (rememberGh && ghStore && ghStore.summary){
    parts.push('MY GITHUB (what this person works on, use it when relevant):\n' + ghStore.summary);
  }
  attachments.forEach(a => {
    if (a.kind === 'text') parts.push('File ' + a.name + '\n' + a.text);
  });
  return parts.join('\n\n').slice(0, 24000);
}

/* ------------------------------------------------------------------ */
/* send                                                                */
/* ------------------------------------------------------------------ */
async function send(){
  const text = input.value.trim();
  if (!text || busy) return;

  busy = true;
  input.value = '';
  autogrow();

  ensureChat(text);
  const chips = $('suggestions');
  if (chips) chips.style.display = 'none';
  hero.style.display = 'none';
  messagesEl.style.display = 'block';

  turns.push({ role: 'user', content: text });
  addUser(text);
  syncChat();

  const history = turns.slice(0, -1).slice(-20).map(m => ({
    role: m.role,
    content: String(m.content || '').slice(0, 3500)
  }));

  const hasImages = attachments.some(a => a.kind === 'image');
  let answer = null;
  let failed = false;
  const bodyEl = addAI();
  if (wantsImageFromText(text, attachments)) {
    bodyEl.innerHTML = '<div class="img-placeholder">‘Making the picture…’</div>';
    const img = await makeImage(text, attachments);
    bodyEl.innerHTML = '';
    if (img && img.image) {
      turns.push({ role:'assistant', content:'', attachments: [{kind:'image', mime_type: 'image/png', data: img.image}] });
      renderAttachments(bodyEl, img.image);
    } else {
      turns.push({ role:'assistant', content: img || 'Vector could not make the picture just now.' });
      bodyEl.innerHTML = plainRender(turns.pop().content);
    }
    syncChat();
    attachActions(bodyEl.parentElement, turns.length - 1);
    attachments = [];
    paintAttachments();
    busy = false;
    autogrow();
    scrollBottom();
    input.focus();
    return;
  }
  bodyEl.innerHTML = '<div class="work-log" id="wl"></div>';
  const wl = $('wl');
  wl && paintWorkLog(wl, text);

  try {
    const res = await credFetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: STYLE + '\n\n' + text,
        provider: hasImages ? 'gemini' : provider,
        history: history,
        memory: memoryPayload(),
        workspace_context: contextPayload(),
        images: attachments.filter(a => a.kind === 'image')
          .slice(0, 4)
          .map(a => ({ mime_type: a.mime_type, data: a.data }))
      })
    });

    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }

    if (!res.ok){
      const detail = data && data.detail;
      answer = (detail && detail.message) || (detail && detail.error) ||
               (typeof detail === 'string' ? detail : '') ||
               ('The server returned ' + res.status + '.');
      failed = true;
    } else {
      answer = (data && (data.response || data.output || data.message)) || 'I did not get an answer back.';
    }
  } catch (e) {
    answer = 'I could not reach the server just now. Check the connection and try again. 🙏';
    failed = true;
  }

  const last = turns.length - 1;
  if (!failed){
    turns.push({ role: 'assistant', content: String(answer) });
    const textEl = document.createElement('div');
    textEl.innerHTML = plainRender(answer);
    bodyEl.parentNode.appendChild(textEl);
  } else {
    bodyEl.innerHTML = plainRender(answer);
  }

  attachActions(bodyEl.parentElement, failed ? -1 : turns.length - 1);
  maybeProjectSuggestion(bodyEl.parentElement, text);

  wlogDone();

  attachments = [];
  paintAttachments();

  busy = false;
  autogrow();
  scrollBottom();
  input.focus();
}

/* ----------------------------- work log ----------------------------- */
let wlTimer = null;
let wlSteps = [];
let wlCurrent = 0;

function paintWorkLog(container, text){
  wlSteps = workLogSteps(text, attachments);
  wlCurrent = 0;
  container.innerHTML = '';
  tickWorkLog(container);
}

function tickWorkLog(container){
  wlTimer && clearInterval(wlTimer);
  if (wlTimer) wlTimer = null;
  if (wlCurrent >= wlSteps.length){
    container.innerHTML = '<span class="done">All done.</span>';
    return;
  }
  wlTimer = setInterval(() => {
    const i = wlCurrent++;
    const el = document.createElement('span');
    el.className = 'wl';
    el.innerHTML = '<span class="spin"></span>' + escapeHtml(wlSteps[i]);
    container.appendChild(el);
    if (container.parentNode){ container.parentNode.scrollTop = container.parentNode.scrollHeight; }
    if (i >= wlSteps.length - 1) {
      clearInterval(wlTimer); wlTimer = null;
      setTimeout(() => { const e = document.createElement('span'); e.className = 'wl done'; e.textContent = 'All done.'; if(container) container.appendChild(e); }, 260);
    }
  }, 430);
}

function wlogDone(){ wlTimer && clearInterval(wlTimer); wlTimer = null; }

function workLogSteps(text, atts){
  const t = (text || '').toLowerCase();
  const steps = [];
  if (/[…∞]/.test(t) || /(infinite loop|never stops|crash loop)/.test(t) || /(hang|freezes?|stuck|lock)/.test(t)){
    steps.push('Checking what is stuck');
  }
  if (/(read|open|load|show|parse|inspect|line [0-9]|function|variable|file |`)/.test(t)) steps.push('Reading the file or code');
  if (/(write|fix|create|change|replace|update|add|build)/.test(t)) steps.push('Writing the fix or file');
  if (/(run|test|exec|compile|deploy|server|npm|node|python)/.test(t)) steps.push('Running the command');
  if (/(server|connection|api|endpoint|http|web|mcp|proxy)/i.test(t)) steps.push('Checking the server connection');
  if (/(browser|html|page|screenshot|visit|url|css|dom)/.test(t)) steps.push('Opening the browser view');
  if (/(diagram|map|draw|architecture|flow|chart)/.test(t)) steps.push('Drawing the diagram');
  if (/(debug|error|break|wrong|bug|throw|crash|fail)/.test(t)) steps.push('Looking for the bug');
  if (!steps.length) steps.push('Thinking about your message');
  steps.push('Building the answer');
  return steps;
}

function maybeProjectSuggestion(bodyEl, text){
  const t = (text || '').toLowerCase();
  if (activeProject) return;
  if (!/(build|make|create|program|project|code|app|website|game|bot|tool|dev|software)/.test(t)) return;
  const card = document.createElement('div');
  card.className = 'project-detect';
  card.innerHTML = '<div style="display:flex;align-items:center;gap:10px">' +
    '<b>This sounds like a build project 🧱</b>' +
    '<button class="set-btn" id="pdMake">Make it a project</button>' +
    '<button class="set-btn" id="pdSkip" style="margin-left:auto">Not right now</button>' +
    '</div>' +
    '<p>Turn this chat into a project so Vector can keep the details with every reply.</p>';
  if (bodyEl && bodyEl.parentNode) bodyEl.parentNode.appendChild(card);
  const close = () => { if(card && card.parentNode) card.remove(); };
  const makeBtn = card && card.querySelector('#pdMake');
  if (makeBtn) makeBtn.onclick = () => { newChat(); openProjects(); close(); toast('Project mode is on — tell me what to build. 🧱'); };
  const skipBtn = card && card.querySelector('#pdSkip');
  if (skipBtn) skipBtn.onclick = close;
}

/* ---------------------------- image ask ---------------------------- */
function wantsImageFromText(text, atts){
  if (atts && atts.some(a => a.kind === 'image')) return false;
  return /\b(draw|create|make|generate|show me a picture|an image of|i need an image|image)/i.test(text || '');
}

async function makeImage(text, atts){
  const prompt = (text || '').trim();
  try {
    const body = { prompt: prompt };
    if (atts && atts.some(a => a.kind === 'image')) {
      body.image = atts.find(a => a.kind === 'image').data;
      body.mime_type = 'image/png';
    }
    const res = await credFetch('/api/image/generate', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const data = await res.json().catch(() => null);
    if (!res.ok || !data || data.status !== 'success') {
      return (data && data.detail && data.detail.message) || (data && data.message) || 'Vector could not make the picture.';
    }
    return data;
  } catch (e) {
    return 'Vector could not reach the picture maker.';
  }
}

function renderAttachments(bodyEl, dataUrl){
  const wrap = document.createElement('div');
  wrap.className = 'img-wrap';
  const img = document.createElement('img');
  img.src = dataUrl; img.alt = 'Generated image';
  const act = document.createElement('div');
  act.className = 'img-actions';
  const closeBtn = document.createElement('button');
  closeBtn.className = 'act'; closeBtn.innerHTML = ICONS.copy; closeBtn.title = 'Keep image';
  closeBtn.onclick = () => { if (navigator.clipboard && navigator.clipboard.write) {
    const raw = atob(dataUrl.split(',')[1]);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    const blob = new Blob([bytes], { type: 'image/png' });
    navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]).then(() => toast('Image kept')).catch(() => toast('Copying blocked'));
  } else { toast('Copying blocked'); } };
  act.appendChild(closeBtn);
  wrap.appendChild(img); wrap.appendChild(act); bodyEl.appendChild(wrap);
}

function shareChat(){
  if (!turns.length){ toast('Nothing to copy yet'); return; }
  const text = turns.map(m => (m.role === 'user' ? 'You: ' : 'Vector: ') + m.content).join('\n\n');
  if (navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(text)
      .then(() => toast('Chat copied — paste it anywhere'))
      .catch(() => toast('Copying is blocked in this browser'));
  } else {
    toast('Copying is not available in this browser');
  }
}

/* ------------------------------------------------------------------ */
/* drawer                                                              */
/* ------------------------------------------------------------------ */
function openDrawer(){
  $('drawer').classList.add('open');
  $('scrim').classList.add('open');
}
function closeDrawer(){
  $('drawer').classList.remove('open');
  $('scrim').classList.remove('open');
}

/* ------------------------------------------------------------------ */
/* account and GitHub                                                  */
/* ------------------------------------------------------------------ */
function providerLabel(p){ return p === 'google' ? 'Google' : 'GitHub'; }

function providerReady(p){ return !!authProviders[p]; }

async function loadAuth(){
  try {
    const res = await credFetch('/api/me');
    if (res.ok){
      const data = await res.json();
      const list = data.providers || {};
      authProviders = {
        github: list.github !== undefined ? !!list.github : data.auth_configured !== false,
        google: !!list.google
      };
      me = data.signed_in ? data.user : null;
    } else {
      authProviders = { github: false, google: false };
    }
  } catch (e) { authProviders = { github: false, google: false }; }
  paintAccount();
}

function startSignIn(p){
  if (providerReady(p)) return true;
  openGate();
  showGateStep('signin');
  paintGateNote(p);
  toast(providerLabel(p) + ' sign-in needs ' + providerLabel(p) + ' keys on the server');
  return false;
}

function paintAccount(){
  const av = $('accountAvatar');
  const nm = $('accountName');
  const plan = $('accountPlan');

  av.style.backgroundImage = '';

  if (me){
    const name = me.name || me.login || 'Signed in';
    nm.textContent = name;
    plan.textContent = providerLabel(me.provider) + ' · ' + (me.login ? '@' + me.login : 'you');
    av.textContent = String(name).slice(0, 2).toUpperCase();
    if (me.avatar_url){
      av.textContent = '';
      av.style.backgroundImage = 'url(' + me.avatar_url + ')';
      av.style.backgroundSize = 'cover';
      av.style.backgroundPosition = 'center';
    }
    $('ghBadge').textContent = 'Connected';
    $('authAction').textContent = 'Sign out';
  } else {
    nm.textContent = memory.name || 'Ramesh';
    plan.textContent = 'Signed out';
    av.textContent = String(memory.name || 'V').slice(0, 1).toUpperCase() || 'V';
    $('ghBadge').textContent = (authProviders.github || authProviders.google) ? 'Connect' : 'Set up';
    $('authAction').textContent = 'Sign in';
  }
  paintGateNote();
}

function openGate(){
  closeAcctMenu();
  loadGateState();
  paintAccount();
  showGateStep(gateAnswers.done ? 'signin' : 'ask');
  $('gate').classList.add('open');
}
function closeGate(){ $('gate').classList.remove('open'); }

function paintHero(){
  const hero = $('hero');
  const messagesEl = $('messages');
  if (hero) hero.style.display = messagesEl && messagesEl.style.display === 'block' ? 'none' : '';
}

function loadGitHubState(){
  // re-read stored GitHub session for drawer display
  const st = readLS('vector.github.v1', {});
  if (st && st.repo){
    ghStore = Object.assign({}, { repo: '', remember: true, summary: '', name: '' }, st);
  }
  $('ghRepo')?.(textContent = ghStore.repo || '');
  $('ghStatus')?.(textContent = ghStore.summary ? 'Summary loaded' : (ghStore.repo ? 'Repo set' : 'Not connected'));
}

function ghBindDrawer(){
  // nothing extra needed; drawer content is built by openGitHubDrawer / paintAccount
}

function openGitHubDrawer(){
  closeAcctMenu();
  $('drawerTitle').textContent = 'GitHub';
  const connected = me && authProviders.github;
  const repo = ghStore.repo || '';
  $('drawerBody').innerHTML = '<div class="drawer-body">' +
    '<div style="padding:6px 0 14px">' +
      '<div style="font-size:13px;color:var(--text-faint);margin-bottom:6px">Your GitHub account, connected to Vector.</div>' +
      '<div style="font-size:15px;font-weight:600">' + (me && me.name ? escapeHtml(me.name) : (me && me.login ? '@' + escapeHtml(me.login) : 'your account')) + '</div>' +
      '<div style="font-size:12.5px;color:var(--text-faint);margin-top:2px">' + (me && me.login ? 'GitHub: @' + escapeHtml(me.login) : '') + '</div>' +
    '</div>' +
    '<div class="set-group">' +
      '<div class="set-row set-wide"><div><div class="lbl">Repo to work in</div><span class="sub">The GitHub repo Vector will read and edit files in.</span></div>' +
        '<input type="text" id="ghRepoInput" value="' + escapeHtml(repo) + '" placeholder="owner/repo" style="min-width:220px"></div></div>' +
      '<div class="set-row"><div><div class="lbl">Remember on this browser</div><span class="sub">Keep this repo bound to this browser.</span></div>' +
        '<label class="switch"><input type="checkbox" id="ghRemember" ' + (ghStore.remember ? 'checked' : '') + '><i></i></label></div></div>' +
      '<div class="set-row"><div><div class="lbl">Load my GitHub summary</div><span class="sub">Let Vector learn about you from GitHub.</span></div>' +
        '<label class="switch"><input type="checkbox" id="ghRememberOpt" ' + (ghStore.remember ? 'checked' : '') + '><i></i></label></div></div>' +
    '</div>' +
    '<div class="set-note">Vector can read your public repo files and make edits when you ask. It does not touch anything else.</div>' +
    '<div style="display:flex;gap:8px;margin-top:6px">' +
      '<button class="set-btn" id="ghSaveRepo" style="flex:1">Save repo</button>' +
      '<button class="set-btn" id="ghLoadSummary" style="flex:1">Refresh summary</button>' +
      '<button class="set-btn danger" id="ghDisconnect" style="flex:1">Disconnect</button>' +
    '</div>' +
    '<hr style="margin:18px 0;border:none;border-top:1px solid var(--border-soft)">' +
    '<div id="ghFileSection" style="display:' + (connected && repo ? 'block' : 'none') + '">' +
      '<div style="font-size:14px;font-weight:600;margin-bottom:8px">Repo files</div>' +
      '<div id="ghFileStatus" style="font-size:12.5px;color:var(--text-faint);margin-bottom:8px"></div>' +
      '<div id="ghFileList" style="max-height:180px;overflow:auto;border:1px solid var(--border-soft);border-radius:10px;background:var(--raise)"></div>' +
      '<div id="ghFileEditor" style="display:none;margin-top:10px">' +
        '<div style="font-size:12.5px;color:var(--text-faint);margin-bottom:4px">File: <span id="ghFilePath" style="color:var(--text);font-weight:600"></span></div>' +
        '<textarea id="ghFileContent" rows="8" style="width:100%;border:1px solid var(--border);border-radius:10px;background:var(--raise);color:var(--text);font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;padding:8px;resize:vertical"></textarea>' +
        '<div style="display:flex;gap:8px;margin-top:8px">' +
          '<button class="set-btn" id="ghEditFile" style="flex:1">Edit with AI</button>' +
          '<button class="set-btn" id="ghSaveFile" style="flex:1">Save to GitHub</button>' +
          '<button class="set-btn" id="ghCloseFile" style="flex:1">Close</button>' +
        '</div>' +
        '<div id="ghFileResult" style="margin-top:8px;font-size:12.5px;color:var(--text-faint)"></div>' +
      '</div>' +
      '<div id="ghFilePrompt" style="display:none;margin-top:10px">' +
        '<div style="font-size:12.5px;color:var(--text-faint);margin-bottom:4px">Ask Vector to change this file:</div>' +
        '<textarea id="ghEditPrompt" rows="3" style="width:100%;border:1px solid var(--border);border-radius:10px;background:var(--raise);color:var(--text);font-family:inherit;font-size:12.5px;padding:8px;resize:vertical" placeholder="e.g. fix the bug in the login function"></textarea>' +
        '<div style="display:flex;gap:8px;margin-top:8px">' +
          '<button class="set-btn" id="ghDoEdit" style="flex:1">Generate edit</button>' +
          '<button class="set-btn" id="ghCancelEdit" style="flex:1">Cancel</button>' +
        '</div>' +
      '</div>' +
    '</div>' +
  '</div>';
  $('drawerBody').dataset.setOpen = '1';
  // wire repo binding
  const repoInput = $('ghRepoInput');
  const saveBtn = $('ghSaveRepo');
  const loadBtn = $('ghLoadSummary');
  const disconnectBtn = $('ghDisconnect');
  const rememberChk = $('ghRemember');
  const rememberOpt = $('ghRememberOpt');
  if (rememberChk) rememberChk.checked = !!ghStore.remember;
  if (rememberOpt) rememberOpt.checked = !!ghStore.remember;
  function save(){
    const r = (repoInput && repoInput.value || '').trim();
    ghStore = Object.assign({}, ghStore, { repo: r, remember: !!(rememberChk && rememberChk.checked) });
    writeLS('vector.github.v1', ghStore);
    loadGitHubState();
    openGitHubDrawer();
    toast('GitHub repo saved: ' + (r || 'none'));
  }
  if (saveBtn) saveBtn.onclick = save;
  if (rememberChk) rememberChk.onchange = () => { if(rememberChk) ghStore.remember = rememberChk.checked; writeLS('vector.github.v1', ghStore); loadGitHubState(); toast(ghStore.remember ? 'GitHub remembered on this browser' : 'GitHub not remembered'); };
  if (rememberOpt) rememberOpt.onchange = () => { if(rememberOpt){ ghStore.remember = rememberOpt.checked; writeLS('vector.github.v1', ghStore); loadGitHubState(); } };
  if (loadBtn) loadBtn.onclick = () => {
    if (!(me && authProviders.github)) { toast('Connect GitHub first'); return; }
    loadBtn.textContent = 'Loading…'; loadBtn.disabled = true;
    credFetch('/api/github/summary')
      .then(r => r.json().catch(() => null))
      .then(data => {
        loadBtn.disabled = false; loadBtn.textContent = 'Refresh summary';
        if (data && data.summary) {
          ghStore.summary = data.summary;
          ghStore.name = data.name || ghStore.name;
          writeLS('vector.github.v1', ghStore);
          toast('GitHub summary loaded ✅');
        } else {
          toast('Could not load summary. Try again.');
        }
      })
      .catch(() => { loadBtn.disabled = false; loadBtn.textContent = 'Refresh summary'; toast('Could not reach GitHub.'); });
  };
  if (disconnectBtn) disconnectBtn.onclick = () => {
    ghStore = { repo: '', remember: false, summary: '', name: '' };
    writeLS('vector.github.v1', ghStore);
    loadGitHubState();
    toast('GitHub disconnected');
  };
  // wire file editor
  const fileSection = $('ghFileSection');
  if (fileSection && connected && repo){
    ghFileLoadList();
  }
  repoInput && (repoInput.onchange = () => { save(); });
}

let ghFileListData = [];
let ghCurrentFile = null;

function ghFileLoadList(){
  const listEl = $('ghFileList');
  const statusEl = $('ghFileStatus');
  const section = $('ghFileSection');
  if (!listEl || !section) return;
  const repo = ghStore.repo || '';
  if (!repo){ section.style.display = 'none'; return; }
  if (!(me && authProviders.github)){ toast('Connect GitHub first'); return; }
  section.style.display = 'block';
  listEl.innerHTML = '<div style="padding:10px;text-align:center;color:var(--text-faint)">Loading files…</div>';
  if (statusEl) statusEl.textContent = 'Reading your repo…';
  credFetch('/api/github/tree?repo=' + encodeURIComponent(repo))
    .then(r => r.json().catch(() => null))
    .then(data => {
      if (statusEl) statusEl.textContent = '';
      if (!data || data.status !== 'success'){
        listEl.innerHTML = '<div style="padding:10px;text-align:center;color:#ff6b6b">Could not read repo. ' + (data && data.message ? data.message : '') + '</div>';
        return;
      }
      ghFileListData = data.files || [];
      if (!ghFileListData.length){
        listEl.innerHTML = '<div style="padding:10px;text-align:center;color:var(--text-faint)">No files found.</div>';
        return;
      }
      listEl.innerHTML = ghFileListData.map(f =>
        '<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 10px;border-bottom:1px solid var(--border-soft);cursor:pointer" onclick="ghFileOpen(\'' + escapeJs(f.path) + '\')">' +
          '<span style="font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:220px">' + escapeHtml(f.path) + '</span>' +
          '<span style="font-size:10.5px;color:var(--text-faint);flex:0 0 auto">' + (f.size > 1000 ? (f.size/1000).toFixed(1) + 'kb' : f.size + 'b') + '</span>' +
        '</div>'
      ).join('');
    })
    .catch(() => {
      if (statusEl) statusEl.textContent = '';
      listEl.innerHTML = '<div style="padding:10px;text-align:center;color:#ff6b6b">Could not reach GitHub.</div>';
    });
}

function ghFileOpen(path){
  const editor = $('ghFileEditor');
  const listEl = $('ghFileList');
  const statusEl = $('ghFileStatus');
  const filePrompt = $('ghFilePrompt');
  const pathEl = $('ghFilePath');
  const contentEl = $('ghFileContent');
  const resultEl = $('ghFileResult');
  if (!editor || !listEl) return;
  if (!(me && authProviders.github)) { toast('Connect GitHub first'); return; }
  ghCurrentFile = path;
  listEl.style.display = 'none';
  editor.style.display = 'block';
  if (filePrompt) filePrompt.style.display = 'none';
  if (pathEl) pathEl.textContent = path;
  if (contentEl) contentEl.value = '';
  if (resultEl) resultEl.textContent = '';
  if (statusEl) statusEl.textContent = 'Reading file…';
  credFetch('/api/github/file?repo=' + encodeURIComponent(ghStore.repo) + '&path=' + encodeURIComponent(path))
    .then(r => r.json().catch(() => null))
    .then(data => {
      if (statusEl) statusEl.textContent = '';
      if (!data || data.status !== 'success'){
        if (contentEl) contentEl.value = '';
        if (resultEl) resultEl.textContent = 'Could not read file. ' + (data && data.message ? data.message : '');
        return;
      }
      if (contentEl) contentEl.value = data.content || '';
    })
    .catch(() => {
      if (statusEl) statusEl.textContent = '';
      if (contentEl) contentEl.value = '';
    });
}

function ghFileClose(){
  const editor = $('ghFileEditor');
  const listEl = $('ghFileList');
  const filePrompt = $('ghFilePrompt');
  const resultEl = $('ghFileResult');
  if (editor) editor.style.display = 'none';
  if (listEl) listEl.style.display = 'block';
  if (filePrompt) filePrompt.style.display = 'none';
  if (resultEl) resultEl.textContent = '';
  ghCurrentFile = null;
  ghFileLoadList();
}

function ghFileEditOpen(){
  const editor = $('ghFileEditor');
  const filePrompt = $('ghFilePrompt');
  const resultEl = $('ghFileResult');
  if (!editor) return;
  editor.style.display = 'none';
  if (filePrompt) filePrompt.style.display = 'block';
  if (resultEl) resultEl.textContent = '';
}

function ghFileEditGenerate(){
  const promptEl = $('ghEditPrompt');
  const filePrompt = $('ghFilePrompt');
  const editor = $('ghFileEditor');
  const resultEl = $('ghFileResult');
  const contentEl = $('ghFileContent');
  const repo = ghStore.repo || '';
  const path = ghCurrentFile;
  if (!path){ toast('No file open'); return; }
  if (!(me && authProviders.github)) { toast('Connect GitHub first'); return; }
  const instruction = (promptEl && promptEl.value || '').trim();
  if (!instruction){ toast('Tell Vector what to change'); return; }
  if (!contentEl) return;
  const content = contentEl.value;
  if (!content){ toast('File is empty'); return; }
  filePrompt && (filePrompt.style.display = 'none');
  if (resultEl) resultEl.textContent = 'Vector is rewriting the file…';
  credFetch('/api/github/edit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo: repo, path: path, instruction: instruction, content: content, language: '' })
  })
  .then(r => r.json().catch(() => null))
  .then(data => {
    if (!data || data.status !== 'success'){
      if (resultEl) resultEl.textContent = 'Edit failed. ' + (data && data.message ? data.message : 'The AI could not rewrite the file.');
      editor && (editor.style.display = 'block');
      return;
      }
    if (contentEl) contentEl.value = data.content || '';
    if (resultEl) resultEl.textContent = 'File rewritten. Press "Save to GitHub" to commit the change.';
    editor && (editor.style.display = 'block');
  })
  .catch(() => {
    if (resultEl) resultEl.textContent = 'Could not reach the editor.';
    editor && (editor.style.display = 'block');
  });
}

function ghFileSave(){
  const contentEl = $('ghFileContent');
  const resultEl = $('ghFileResult');
  const repo = ghStore.repo || '';
  const path = ghCurrentFile;
  if (!path){ toast('No file open'); return; }
  if (!(me && authProviders.github)) { toast('Connect GitHub first'); return; }
  const content = (contentEl && contentEl.value || '').trim();
  if (!content){ toast('File is empty'); return; }
  if (resultEl) resultEl.textContent = 'Saving to GitHub…';
  credFetch('/api/github/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo: repo, path: path, content: content, message: 'Update with Vector AI' })
  })
  .then(r => r.json().catch(() => null))
  .then(data => {
    if (!data || data.status !== 'success'){
      if (resultEl) resultEl.textContent = 'Save failed. ' + (data && data.message ? data.message : 'Could not save to GitHub.');
      return;
    }
    if (resultEl) resultEl.textContent = 'Saved to GitHub ✅ ' + (data.url || '');
    toast('File saved to GitHub');
  })
  .catch(() => {
    if (resultEl) resultEl.textContent = 'Could not reach GitHub.';
  });
}


function clearChatHistory(){
  if (!confirm('Clear the whole chat? This cannot be undone.')) return;
  turns = [];
  attachments = [];
  paintAttachments();
  $('messages').innerHTML = '';
  $('messages').style.display = 'none';
  $('hero').style.display = '';
  $('suggestions') && ($('suggestions').style.display = '');
  writeLS(LS.chat, null);
  toast('Chat cleared');
}

function resetMemory(){
  if (!confirm('Forget my name, age, and notes? This cannot be undone.')) return;
  memory = {};
  gateAnswers = { name: '', age: '', build: '', done: false };
  notes = [];
  writeLS(LS.mem, memory);
  writeLS(GATE_LS, gateAnswers);
  writeLS(LS.notes, notes);
  paintAccount();
  paintMemoryBadge();
  toast('Memory reset');
}

function clearSavedFiles(){
  attachments = [];
  paintAttachments();
  toast('Saved files cleared');
}

function viewSessions(){
  toast('Sessions are managed on the server. Sign out here to leave this browser.');
}


function authAction(){
  if (me) signOut();
  else openGate();
}

async function signOut(){
  try { await credFetch('/auth/logout', { method: 'POST' }); } catch (e) {}
  me = null;
  paintAccount();
  toast('Signed out');
}

function toggleAcctMenu(e){
  if (e) e.stopPropagation();
  $('acctMenu').classList.toggle('open');
}
function closeAcctMenu(){ $('acctMenu').classList.remove('open'); }

document.addEventListener('click', e => {
  const menu = $('acctMenu');
  if (menu.classList.contains('open') && !menu.contains(e.target)) closeAcctMenu();
});

/* ------------------------------------------------------------------ */
/* sidebar escape                                                      */
/* ------------------------------------------------------------------ */
function syncSide(){
  const closed = sidebar.classList.contains('hidden');
  const scrim = $('sideScrim');
  if (scrim) scrim.classList.toggle('open', !closed && window.innerWidth <= 900);
  document.body.classList.toggle('side-open', !closed);
}

function closeSidebar(){
  sidebar.classList.add('hidden');
  syncSide();
}

function closeSidebarOnMobile(){
  if (window.innerWidth <= 900) closeSidebar();
}

window.addEventListener('resize', syncSide);

/* ----------------------------- github remember helper ----------------------------- */
function rememberGh(){
  return !!(ghStore && ghStore.remember);
}

/* ----------------------------- agents ---------------------------- */
const AGENTS = [
  { id:'chat', name:'Agent', tip:'General help, calm and clear' },
  { id:'code', name:'Code', tip:'Write, change, and explain code' },
  { id:'debug', name:'Debug', tip:'Find why things break' },
  { id:'review', name:'Review', tip:'Review your code and suggest fixes' },
  { id:'diagram', name:'Diagram', tip:'Draw architecture and flow pictures' },
  { id:'image', name:'Image', tip:'Make and edit pictures' }
];

function openAgents(){
  const menu = $('agentsMenu');
  if (!menu) return;
  menu.classList.toggle('open');
}

function chooseAgent(id){
  mode = id;
  $('modeLabel').textContent = (AGENTS.find(a => a.id === id) || {}).name || 'Agent';
  setGreeting(mode);
  const a = AGENTS.find(x => x.id === id);
  toast('Using Agent: ' + (a ? a.name : 'Agent') + (a && a.tip ? ' — ' + a.tip : ''));
  closeAgents();
}

function closeAgents(){
  const menu = $('agentsMenu');
  if (menu) menu.classList.remove('open');
}

document.addEventListener('click', e => {
  if (e.target && e.target.closest && !e.target.closest('#agentsBtn') && !e.target.closest('#agentsMenu')) closeAgents();
});

/* ------------------------------------------------------------------ */
/* sign-in questions and buttons                                       */
/* ------------------------------------------------------------------ */
const GATE_LS = 'vector.gate.v1';
let gateAnswers = { name: '', age: '', build: '', done: false };

function loadGateState(){
  const saved = readLS(GATE_LS, null);
  if (saved && typeof saved === 'object') gateAnswers = Object.assign(gateAnswers, saved);

  const nameEl = $('gName');
  const ageEl = $('gAge');
  const buildEl = $('gBuild');
  if (nameEl) nameEl.value = gateAnswers.name || memory.name || '';
  if (ageEl) ageEl.value = gateAnswers.age || memory.age || '';
  if (buildEl) buildEl.value = gateAnswers.build || '';
}

function showGateStep(step){
  const ask = $('gateAsk');
  const signin = $('gateSignin');
  if (!ask || !signin) return;
  ask.style.display = step === 'ask' ? 'block' : 'none';
  signin.style.display = step === 'signin' ? 'block' : 'none';
}

function paintGateNote(provider){
  const note = $('gateNote');
  if (!note) return;

  if (me){
    note.textContent = 'Signed in as ' + (me.name || me.login || 'you') +
      ' with ' + providerLabel(me.provider) + ' ✅';
    return;
  }

  const missing = ['github', 'google'].filter(p => !authProviders[p]);
  if (!missing.length){
    note.textContent = 'Both buttons are ready. Pick whichever account you like. 👇';
    return;
  }
  if (provider && !authProviders[provider]){
    note.textContent = providerLabel(provider) + ' sign-in is not switched on: the app owner still has to add ' +
      (provider === 'google' ? 'GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET' : 'GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET') +
      ' on the server. You can still use Vector without signing in.';
    return;
  }
  if (missing.length === 2){
    note.textContent = 'Sign-in is not switched on yet. The app owner still has to add GitHub or Google keys on the server. You can still use Vector without signing in.';
    return;
  }
  note.textContent = providerLabel(missing[0]) + ' sign-in needs keys on the server, but ' +
    providerLabel(missing[0] === 'github' ? 'google' : 'github') + ' is ready to use.';
}

function openGateAsk(){
  loadGateState();
  showGateStep('ask');
}

function saveGateAnswers(){
  const name = ($('gName').value || '').trim();
  const age = ($('gAge').value || '').trim();
  const build = ($('gBuild').value || '').trim();

  if (!name){ toast('Tell me your name first 🙂'); $('gName').focus(); return; }

  memory.name = name;
  if (age) memory.age = age;
  if (build) memory.about = build;
  writeLS(LS.mem, memory);

  gateAnswers = { name: name, age: age, build: build, done: true };
  writeLS(GATE_LS, gateAnswers);

  notes = notes.filter(n => n.indexOf('Wants to build:') !== 0);
  if (build) notes.unshift('Wants to build: ' + build);
  notes = notes.slice(0, 20);
  writeLS(LS.notes, notes);

  paintMemoryBadge();
  paintAccount();
  showGateStep('signin');
  toast('Thanks ' + name + ' 👋 I will remember that');
}

function skipGateQuestions(){
  gateAnswers.done = true;
  writeLS(GATE_LS, gateAnswers);
  showGateStep('signin');
}

/* ------------------------------------------------------------------ */
/* live voice                                                          */
/* ------------------------------------------------------------------ */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let liveOn = false;
let recState = 'idle';
let rec = null;
let micTapCount = 0;
let micTimer = null;
let micStream = null;
let camStream = null;
let soundActive = false;
let soundAnim = null;

function openLive(){
  closeAcctMenu();
  closeDrawer();
  liveOn = true;
  $('liveAnswer').textContent = '';
  $('live').classList.add('open');
  micTapCount = 0;
  if (micTimer) clearTimeout(micTimer);
  micTimer = null;
  soundActive = false;
  if (soundAnim) clearInterval(soundAnim);
  soundAnim = null;
  $('liveStatus').textContent = SR
    ? 'Tap the circle twice to use the microphone.'
    : 'This browser has no voice input, so type below and Vector will answer out loud.';
  $('liveTools').innerHTML = liveToolsHtml();
  $('liveCamWrap').classList.remove('on');
  $('soundBar').classList.remove('on');
  stopRec();
  stopSound();
  if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  if (camStream) { camStream.getTracks().forEach(t => t.stop()); camStream = null; }
  $('liveCamVideo').srcObject = null;
}

function closeLive(){
  liveOn = false;
  $('live').classList.remove('open');
  stopRec();
  stopSound();
  if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  if (camStream) { camStream.getTracks().forEach(t => t.stop()); camStream = null; }
  $('liveCamVideo').srcObject = null;
  $('liveCamWrap').classList.remove('on');
  if ('speechSynthesis' in window){
    try { speechSynthesis.cancel(); } catch (e) {}
  }
}

function setOrb(on){ $('liveOrb').classList.toggle('rec', !!on); }

function liveToggle(){
  if (recState === 'listening'){
    stopRec();
    $('liveStatus').textContent = 'Paused. Tap the circle again to resume.';
    return;
  }
  handleMicTap();
}

function handleMicTap(){
  micTapCount++;
  if (micTapCount === 1){
    $('liveStatus').textContent = 'Tap the circle one more time to turn on the microphone.';
    if (micTimer) clearTimeout(micTimer);
    micTimer = setTimeout(() => { micTapCount = 0; $('liveStatus').textContent = SR ? 'Tap the circle twice to use the microphone.' : 'This browser has no voice input.'; }, 1400);
    return;
  }
  if (micTapCount === 2){
    if (micTimer) clearTimeout(micTimer);
    micTapCount = 0;
    startMic();
  }
}

function startMic(){
  if (!SR){
    $('liveStatus').textContent = 'This browser has no voice input. Type below instead.';
    $('liveText').focus();
    return;
  }
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia){
    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(stream => { micStream = stream; beginListening(); })
      .catch(err => {
        if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError'){
          $('liveStatus').textContent = 'Microphone is blocked. Allow it in the browser, or type below.';
        } else {
          $('liveStatus').textContent = 'I could not start the microphone. Type below instead.';
        }
      });
  } else {
    beginListening();
  }
}

function beginListening(){
  try { rec = new SR(); } catch (e) { $('liveStatus').textContent = 'I could not start the microphone.'; return; }
  rec.lang = 'en-IN';
  rec.interimResults = true;
  rec.continuous = false;
  rec.onstart = () => { recState = 'listening'; setOrb(true); startSound(); $('liveStatus').textContent = 'Listening…'; };
  rec.onresult = ev => {
    let said = '';
    for (let i = 0; i < ev.results.length; i++) said += ev.results[i][0].transcript;
    $('liveStatus').textContent = said;
    const last = ev.results[ev.results.length - 1];
    if (last && last.isFinal){
      stopRec();
      stopSound();
      liveSend(said);
    }
  };
  rec.onerror = ev => {
    stopRec();
    stopSound();
    setOrb(false);
    recState = 'idle';
    $('liveStatus').textContent = ev.error === 'not-allowed'
      ? 'Microphone is blocked. Allow it in the browser, or type below.'
      : 'I did not catch that — tap the circle twice and try again.';
  };
  rec.onend = () => { setOrb(false); if (recState === 'listening') recState = 'idle'; };
  try { rec.start(); } catch (e) { $('liveStatus').textContent = 'Tap the circle twice to start listening.'; }
}

function stopRec(){
  if (rec){
    try { rec.onend = null; rec.stop(); } catch (e) {}
    rec = null;
  }
  recState = 'idle';
  setOrb(false);
}

function liveToolsHtml(){
  return '<div class="live-tools">' +
    '<button class="live-tool" id="liveMicBtn" title="Microphone (tap twice)">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/></svg>' +
    '</button>' +
    '<button class="live-tool" id="liveCamBtn" title="Camera">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M23 7 16 12 23 17 23 7Z"/><rect x="1" y="5" width="14" height="14" rx="2"/></svg>' +
    '</button>' +
    '</div>' +
    '<div class="sound-bar" id="soundBar">' +
      '<i></i><i></i><i></i><i></i><i></i><i></i>' +
    '</div>' +
    '<div class="live-cam-wrap" id="liveCamWrap">' +
      '<video id="liveCamVideo" autoplay playsinline muted></video>' +
      '<button class="set-btn" id="liveCamOff" style="margin-top:8px">Turn off camera</button>' +
    '</div>' +
    '<div class="live-hint">Tap the circle twice for the mic. Turn on the camera to include a photo with your voice note.</div>';
}

function startSound(){
  if (soundActive) return;
  soundActive = true;
  $('soundBar').classList.add('on');
  const bars = $('soundBar').querySelectorAll('i');
  if (soundAnim) clearInterval(soundAnim);
  soundAnim = setInterval(() => {
    bars.forEach((b, i) => {
      const h = 3 + Math.floor(Math.random() * 22);
      b.style.height = h + 'px';
      b.style.opacity = 0.4 + Math.random() * 0.6;
    });
  }, 90);
}

function stopSound(){
  soundActive = false;
  if (soundAnim) clearInterval(soundAnim);
  soundAnim = null;
  $('soundBar').classList.remove('on');
  const bars = $('soundBar').querySelectorAll('i');
  bars.forEach(b => { b.style.height = '4px'; b.style.opacity = '0.4'; });
}

function startCamera(){
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
    toast('This browser cannot use the camera');
    return;
  }
  $('liveCamBtn').disabled = true;
  navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' } })
    .then(stream => {
      camStream = stream;
      $('liveCamVideo').srcObject = stream;
      $('liveCamWrap').classList.add('on');
      $('liveCamBtn').classList.add('on');
      $('liveCamBtn').disabled = false;
      toast('Camera on — your photo will be included');
    })
    .catch(err => {
      $('liveCamBtn').disabled = false;
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError'){
        toast('Camera is blocked. Allow it in the browser, or turn it off.');
      } else {
        toast('I could not start the camera.');
      }
    });
}

function stopCamera(){
  if (camStream){ camStream.getTracks().forEach(t => t.stop()); camStream = null; }
  $('liveCamVideo').srcObject = null;
  $('liveCamWrap').classList.remove('on');
  const camBtn = $('liveCamBtn');
  if (camBtn) camBtn.classList.remove('on');
}

function liveToggleMic(){
  if (recState === 'listening'){
    stopRec(); stopSound();
    $('liveMicBtn').classList.remove('on');
    $('liveStatus').textContent = 'Paused. Tap the circle again to resume.';
    return;
  }
  handleMicTap();
}

$('liveMicBtn') && ($('liveMicBtn').onclick = liveToggleMic);
$('liveCamBtn') && ($('liveCamBtn').onclick = () => {
  if ($('liveCamWrap').classList.contains('on')){ stopCamera(); toast('Camera off'); }
  else { startCamera(); }
});
$('liveCamOff') && ($('liveCamOff').onclick = () => { stopCamera(); toast('Camera off'); });

async function liveSend(text){
  const question = String(text || '').trim();
  if (!question) return;
  $('liveText').value = '';
  $('liveStatus').textContent = 'Thinking…';
  $('liveAnswer').textContent = '';
  stopSound();
  let imageData = null;
  if (camStream){
    try {
      const track = camStream.getVideoTracks()[0];
      if (track){
        const blob = new Blob([], { type: 'video/webm' });
        const recorder = new MediaRecorder(track, { mimeType: MediaRecorder.isTypeSupported('video/webm;codecs=vp8') ? 'video/webm;codecs=vp8' : 'video/webm' });
        const chunks = [];
        recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
        recorder.onstop = () => {
          const b = new Blob(chunks, { type: recorder.mimeType });
          const fr = new FileReader();
          fr.onload = () => { imageData = fr.result.split(',')[1] || null; };
          fr.readAsDataURL(b);
        };
        recorder.start();
        setTimeout(() => { try { recorder.stop(); } catch (e) {} }, 180);
      }
    } catch (e) { imageData = null; }
  }
  ensureChat(question);
  turns.push({ role: 'user', content: question });
  syncChat();
  try {
    const body = { prompt: STYLE + '\n\nAnswer this out loud, so keep it to two or three short sentences: ' + question };
    if (imageData){ body.images = [{ mime_type: 'image/png', data: imageData }]; }
    const res = await credFetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok){
      const detail = data && data.detail;
      $('liveStatus').textContent = (detail && (detail.message || detail.error)) || 'The server did not answer.';
      return;
    }
    const answer = (data && (data.response || data.output || data.message)) || 'No answer came back.';
    $('liveAnswer').innerHTML = plainRender(answer);
    $('liveStatus').textContent = 'Tap the circle to talk again.';
    turns.push({ role: 'assistant', content: String(answer) });
    syncChat();
    paintThread();
    speakOut(answer);
  } catch (e) {
    $('liveStatus').textContent = 'I could not reach the server just now.';
  }
}

function speakOut(text){
  if (!('speechSynthesis' in window)) return;
  const clean = String(text)
    .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/gu, '')
    .replace(/[•*#`]/g, '')
    .slice(0, 700);
  try {
    speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(clean);
    utter.rate = 1.02;
    utter.pitch = 1;
    speechSynthesis.speak(utter);
  } catch (e) {}
}
/* ----------------------------- settings wiring ----------------------------- */
function bindSetRows(name){
  const nav = $('setNav');
  const main = $('setMain');
  if (!nav || !main) return;
  nav.onclick = (e) => { const b = e.target.closest('[data-set-id]'); if (!b) return; selectSection(b.getAttribute('data-set-id')); };
  const item = readLS('vector.settings.v1', {});
  selectSection(item.section || 'General');
}

function stAccentsPick(value){
  const shades = ['#ffffff','#60a5fa','#22d3ee','#34d399','#fbbf24','#fb923c','#f87171','#a78bfa'];
  const wrap = $('stAccents');
  if (!wrap) return;
  wrap.innerHTML = shades.map(c => '<span class="swatch' + (c.toLowerCase() === value.toLowerCase() ? ' on' : '') + '" style="background:' + c + '" onclick="setAccent(\'' + c + '\')" />').join('');
}

function setAccent(value){
  writeLS('vector.settings.v1', Object.assign({}, readLS('vector.settings.v1', {}), { accent: value }));
  applySettings();
  stAccentsPick(value);
  toast('Accent colour saved');
}

function applySettings(){
  const item = readLS('vector.settings.v1', {});
  const theme = item.theme || 'System';
  const accent = item.accent || '#ffffff';
  document.documentElement.setAttribute('data-theme', theme === 'System' ? 'dark' : theme.toLowerCase());
  document.documentElement.style.setProperty('--accent', accent);
}

function setTheme(value){
  writeLS('vector.settings.v1', Object.assign({}, readLS('vector.settings.v1', {}), { theme: value }));
  applySettings();
  toast('Theme applied');
}

function setLang(value){
  writeLS('vector.settings.v1', Object.assign({}, readLS('vector.settings.v1', {}), { language: value }));
  toast('Language set to ' + value);
}

function setVoice(value){
  writeLS('vector.settings.v1', Object.assign({}, readLS('vector.settings.v1', {}), { voice: value }));
  toast('Voice: ' + value);
}

function openSettings(){
  closeAcctMenu();
  closeDrawer();
  $('drawerTitle').textContent = 'Settings';
  $('drawerBody').innerHTML = settingsTemplate();
  paintSettings();
  $('drawerBody').dataset.setOpen = '1';
}

function closeSettings(){
  const body = $('drawerBody');
  if (body) body.dataset.setOpen = '';
  closeDrawer();
}

/* ----------------------------- settings template ----------------------------- */
const SET_SECTIONS = [
  { id:'General', label:'General' },
  { id:'Personalization', label:'Personalization' },
  { id:'Apps', label:'Apps & connectors' },
  { id:'Data', label:'Data controls' },
  { id:'Security', label:'Security' },
  { id:'Account', label:'Account' },
  { id:'Parental', label:'Parental controls' }
];

function settingsTemplate(){
  return '<div class="settings-card">' +
    '<button class="icon-btn settings-close" onclick="closeSettings()" title="Close settings">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>' +
    '</button>' +
    '<nav class="settings-nav" id="setNav">' +
      SET_SECTIONS.map(s => '<button data-set-id="' + s.id + '">' + s.label + '</button>').join('') +
    '</nav>' +
    '<div class="settings-main" id="setMain"></div>' +
  '</div>';
}

function paintSettings(){
  bindSetRows();
}

let currentSetSection = null;

function selectSection(id){
  currentSetSection = id;
  const nav = $('setNav');
  if (nav){
    nav.querySelectorAll('button[data-set-id]').forEach(b => b.classList.toggle('active', b.getAttribute('data-set-id') === id));
  }
  const main = $('setMain');
  if (!main) return;
  writeLS('vector.settings.v1', Object.assign({}, readLS('vector.settings.v1', {}), { section: id }));
  const item = readLS('vector.settings.v1', {});
  const render = {
    'General': () => '<div class="set-group">' +
      '<div class="set-row set-wide"><div><div class="lbl">Name</div><span class="sub">What Vector calls you</span></div>' +
        '<input type="text" id="stName" value="' + (memory.name || '') + '" placeholder="Your name"></div></div>' +
      '<div class="set-row set-wide"><div><div class="lbl">How old are you?</div><span class="sub">Helps Vector give better answers</span></div>' +
        '<input type="text" id="stAge" value="' + (memory.age || '') + '" placeholder="e.g. 12"></div></div>' +
      '<div class="set-row set-wide"><div><div class="lbl">About you</div><span class="sub">Anything you want Vector to know</span></div>' +
        '<textarea id="stAbout" rows="3" placeholder="e.g. I like building games">' + (memory.about || '') + '</textarea></div></div>' +
      '<div class="set-row set-wide"><div><div class="lbl">Language</div><span class="sub">The language Vector uses with you</span></div>' +
        '<select id="stLang">' +
          '<option value="English"' + (item.language === 'English' ? ' selected' : '') + '>English</option>' +
          '<option value="Hindi"' + (item.language === 'Hindi' ? ' selected' : '') + '>Hindi</option>' +
          '<option value="Tamil"' + (item.language === 'Tamil' ? ' selected' : '') + '>Tamil</option>' +
        '</select></div></div>' +
      '<div class="set-row"><div><div class="lbl">Start Vector when you open the page</div><span class="sub">Opens the chat right away</span></div>' +
        '<label class="switch"><input type="checkbox" id="stAutoStart" ' + (item.auto_start ? 'checked' : '') + '><i></i></label></div></div>' +
      '<div class="set-row"><div><div class="lbl">Play sound when Vector answers</div><span class="sub">A small beep when it is done</span></div>' +
        '<label class="switch"><input type="checkbox" id="stSound" ' + (item.sound_on ? 'checked' : '') + '><i></i></label></div></div>' +
    '</div>' +
    '<button class="set-btn" onclick="saveGeneral()">Save changes</button>',
    'Personalization': () => '<div class="set-group">' +
      '<div class="set-row set-wide"><div><div class="lbl">Theme</div><span class="sub">Light, dark, or follow your device</span></div>' +
        '<select id="stTheme">' +
          '<option value="System"' + (item.theme === 'System' ? ' selected' : '') + '>System default</option>' +
          '<option value="Dark"' + (item.theme === 'Dark' ? ' selected' : '') + '>Dark</option>' +
          '<option value="Light"' + (item.theme === 'Light' ? ' selected' : '') + '>Light</option>' +
        '</select></div></div>' +
      '<div class="set-row set-wide"><div><div class="lbl">Accent colour</div><span class="sub">The blue-purple in buttons and bars</span></div>' +
        '<div class="swatches" id="stAccents"></div></div>' +
      '<div class="set-row set-wide"><div><div class="lbl">How Vector sounds</div><span class="sub">Friendly, calm, and clear for you</span></div>' +
        '<select id="stVoice">' +
          '<option value="Friendly"' + (item.voice === 'Friendly' ? ' selected' : '') + '>Friendly</option>' +
          '<option value="Calm"' + (item.voice === 'Calm' ? ' selected' : '') + '>Calm</option>' +
          '<option value="Curious"' + (item.voice === 'Curious' ? ' selected' : '') + '>Curious</option>' +
        '</select></div></div>' +
      '<div class="set-row"><div><div class="lbl">Show suggestion chips at the start</div><span class="sub">Quick ideas when you open a chat</span></div>' +
        '<label class="switch"><input type="checkbox" id="stChips" ' + (item.show_chips !== false ? 'checked' : '') + '><i></i></label></div></div>' +
    '</div>' +
    '<div class="set-note">Your accent, theme, and voice are saved on this device only.</div>',
    'Apps': () => '<div class="set-group">' +
      '<div class="set-row set-wide"><div><div class="lbl">GitHub</div><span class="sub">Edit files in your GitHub repos and let Vector learn about you from GitHub.</span></div>' +
        '<button class="set-btn" id="ghConnectBtn">' +
          ((authProviders.github && me) ? 'Connected' : 'Connect GitHub') +
        '</button></div></div>' +
      '<div class="set-row set-wide"><div><div class="lbl">Google</div><span class="sub">Sign in with your Google account.</span></div>' +
        '<button class="set-btn" id="glConnectBtn">' +
          ((authProviders.google && me) ? 'Connected' : 'Connect Google') +
        '</button></div></div>' +
      '<div class="set-row"><div><div class="lbl">Remember GitHub on this browser</div><span class="sub">Keep your GitHub repo bound to this browser</span></div>' +
        '<label class="switch"><input type="checkbox" id="stGhRemember" ' + (ghStore && ghStore.remember ? 'checked' : '') + '><i></i></label></div></div>' +
    '</div>' +
    '<div class="set-note">GitHub and Google are used only to sign you in and make the features you choose work.</div>',
    'Data': () => '<div class="set-group">' +
      '<div class="set-row set-wide"><div><div class="lbl">Clear chat history</div><span class="sub">Start fresh. This cannot be undone.</span></div>' +
        '<button class="set-btn danger" onclick="clearChatHistory()">Clear history</button></div></div>' +
      '<div class="set-row set-wide"><div><div class="lbl">Reset memory</div><span class="sub">Forget your name, age, and notes.</span></div>' +
        '<button class="set-btn danger" onclick="resetMemory()">Reset memory</button></div></div>' +
      '<div class="set-row set-wide"><div><div class="lbl">Delete saved files</div><span class="sub">Remove files you attached before.</span></div>' +
        '<button class="set-btn danger" onclick="clearSavedFiles()">Clear files</button></div></div>' +
    '</div>' +
    '<div class="set-note">Your chats are kept on this device and in your signed-in account when you sign in.</div>',
    'Security': () => '<div class="set-group">' +
      '<div class="set-row set-wide"><div><div class="lbl">Sign-in key</div><span class="sub">' +(me ? providerLabel(me.provider) + ' is connected. ' : '') +
          'Only you can sign in on this browser.</div></div>' +
        '<button class="set-btn" onclick="viewSessions()">Manage sessions</button></div></div>' +
      '<div class="set-row"><div><div class="lbl">Two-step sign-in</div><span class="sub">Extra protection when you sign in.</span></div>' +
        '<label class="switch"><input type="checkbox" id="st2fa" ' + (item.twofa ? 'checked' : '') + '><i></i></label></div></div>' +
      '<div class="set-row"><div><div class="lbl">Lock Vector after inactivity</div><span class="sub">Ask for sign-in again after a while.</span></div>' +
        '<label class="switch"><input type="checkbox" id="stLock" ' + (item.lock_after ? 'checked' : '') + '><i></i></label></div></div>' +
    '</div>' +
    '<div class="set-note">Your sign-in is protected by ' + providerLabel(me && me.provider) + '.</div>',
    'Account': () => '<div class="set-group">' +
      '<div class="set-row set-wide"><div><div class="lbl">Signed in as</div><span class="sub">Your account on this browser</span></div>' +
        '<div style="font-size:13px">' + (me ? (me.name || me.login) + ' (' + providerLabel(me.provider) + ')' : 'Signed out') + '</div></div></div>' +
      '<div class="set-row set-wide"><div><div class="lbl">Email</div><span class="sub">The email on your account</span></div>' +
        '<div style="font-size:13px">' + (me && me.email ? me.email : '—') + '</div></div></div>' +
      '<div class="set-row set-wide"><div><div class="lbl">Plan</div><span class="sub">Free</span></div>' +
        '<div style="font-size:13px">Free</div></div></div>' +
    '</div>' +
    '<div class="set-note">Your account is kept safe by your sign-in provider.</div>',
    'Parental': () => '<div class="set-group">' +
      '<div class="set-row set-wide"><div><div class="lbl">Parental controls</div><span class="sub">Help keep things safe when you are younger.</span></div>' +
        '<label class="switch"><input type="checkbox" id="stParental" ' + (item.parental ? 'checked' : '') + '><i></i></label></div></div>' +
      '<div class="set-row set-wide"><div><div class="lbl">Who can message you</div><span class="sub">Only people you know.</span></div>' +
        '<select id="stWhoCanMessage">' +
          '<option value="Everyone"' + (item.who_can_message === 'Everyone' ? ' selected' : '') + '>Everyone</option>' +
          '<option value="People you know"' + (item.who_can_message === 'People you know' ? ' selected' : '') + '>People you know</option>' +
          '<option value="No one"' + (item.who_can_message === 'No one' ? ' selected' : '') + '>No one</option>' +
        '</select></div></div>' +
      '<div class="set-row set-wide"><div><div class="lbl">Ask a parent before sharing</div><span class="sub">Vector will ask before sharing anything.</span></div>' +
        '<label class="switch"><input type="checkbox" id="stAskParent" ' + (item.ask_parent ? 'checked' : '') + '><i></i></label></div></div>' +
    '</div>' +
    '<div class="set-note">These settings help keep your experience safe.</div>',
  }[id] || (() => '<div class="set-note">This section is not ready yet.</div>');
  main.innerHTML = render();
  // wire accent re-paint after render
  if (id === 'Personalization'){ stAccentsPick(item.accent || '#ffffff'); }
  // wire GitHub connect button
  const ghBtn = $('ghConnectBtn');
  if (ghBtn){
    ghBtn.onclick = () => { if (me && authProviders.github){ openGitHubDrawer(); } else { openGate(); showGateStep('signin'); paintGateNote('github'); toast('Connect GitHub to use this feature'); } };
  }
  const glBtn = $('glConnectBtn');
  if (glBtn){
    glBtn.onclick = () => { if (me && authProviders.google){ toast('Google is already connected'); } else { openGate(); showGateStep('signin'); paintGateNote('google'); toast('Connect Google to use this feature'); } };
  }
}

function saveGeneral(){
  const name = ($('stName').value || '').trim();
  const age = ($('stAge').value || '').trim();
  const about = ($('stAbout').value || '').trim();
  const lang = ($('stLang').value || 'English');
  const autoStart = $('stAutoStart').checked;
  const sound = $('stSound').checked;
  if (name){ memory.name = name; } else { toast('Tell me your name first 🙂'); $('stName').focus(); return; }
  if (age) memory.age = age;
  if (about) memory.about = about;
  writeLS(LS.mem, memory);
  writeLS('vector.settings.v1', Object.assign(readLS('vector.settings.v1', {}), {
    language: lang, auto_start: autoStart, sound_on: sound
  }));
  gateAnswers.name = name; gateAnswers.age = age; gateAnswers.build = about;
  writeLS(GATE_LS, gateAnswers);
  paintAccount(); paintMemoryBadge();
  toast('Saved');
  setTimeout(() => selectSection('General'), 50);
}

/* ------------------------------------------------------------------ */
/* keyboard and boot                                                   */
/* ------------------------------------------------------------------ */
document.addEventListener('keydown', e => {
  if (e.key === 'Escape'){
    closeDrawer(); closeGate(); closeLive(); closeAcctMenu(); closeSidebar();
  }
});

function boot(){
  loadState();
  paintAttachments();
  paintProject();
  setGreeting(mode);
  autogrow();

  closeSidebarOnMobile();

  applySettings();
  ghStore = readLS('vector.github.v1', {}) || {};
  if (!ghStore) ghStore = { repo: '', remember: true, summary: '', name: '' };
  else ghStore = Object.assign({ repo: '', remember: true, summary: '', name: '' }, ghStore);
  loadAuth();
  loadProviders();
  loadGitHubState();
  ghBindDrawer();
  syncSide();
  paintHero();
  $('liveMicBtn') && ($('liveMicBtn').onclick = liveToggleMic);
  $('liveCamBtn') && ($('liveCamBtn').onclick = () => {
    if ($('liveCamWrap').classList.contains('on')){ stopCamera(); toast('Camera off'); }
    else { startCamera(); }
  });
  $('liveCamOff') && ($('liveCamOff').onclick = () => { stopCamera(); toast('Camera off'); });

  const params = new URLSearchParams(window.location.search);
  const auth = params.get('auth');
  const who = providerLabel(params.get('provider'));
  if (auth === 'ok'){
    toast('Signed in with ' + who + ' 🎉');
  } else if (auth === 'unconfigured'){
    openGate();
    showGateStep('signin');
    paintGateNote(params.get('provider'));
    toast(who + ' sign-in needs ' + who + ' keys on the server');
  } else if (auth === 'error'){
    openGate();
    showGateStep('signin');
    toast(who + ' sign-in did not finish — try again');
  }
  if (auth){
    params.delete('auth');
    params.delete('provider');
    const q = params.toString();
    window.history.replaceState({}, '', window.location.pathname + (q ? '?' + q : ''));
  }
}

boot();
