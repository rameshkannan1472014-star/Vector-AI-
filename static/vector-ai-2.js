/* ================================================================== */
/* Vector AI front end                                                 */
/* ================================================================== */
const $ = id => document.getElementById(id);

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

/* replies stay plain text + emojis, the way Ramesh asked for */
const STYLE = 'Reply in short, friendly plain text with emojis. ' +
              'Never use markdown: no asterisks, no #, no code blocks, no bullet dashes. ' +
              'Explain it simply, like you are talking to a 12 year old.';

let busy = false;
let mode = 'chat';
let provider = 'gemini';
let providerList = [];

let turns = [];          /* messages of the open chat */
let activeId = null;     /* id of the open chat   */
let chats = [];          /* every saved chat      */

let memory = { name: 'Ramesh', age: '12', about: '', preferences: '' };
let notes = [];
let projects = [];
let activeProject = null;
let attachments = [];

let me = null;
let authConfigured = false;

/* ------------------------------------------------------------------ */
/* small helpers                                                       */
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

/* plain text only: strip markdown marks, keep line breaks and emojis */
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

function toggleSidebar(){ sidebar.classList.toggle('hidden'); }

/* ------------------------------------------------------------------ */
/* modes                                                               */
/* ------------------------------------------------------------------ */
const MODES = ['chat','code','debug','review','diagram','image'];

function labelFor(m){
  return { chat:'Agent', code:'Code', debug:'Debug', review:'Review', diagram:'Diagram', image:'Image' }[m] || 'Agent';
}
function setGreeting(m){
  const copy = {
    chat:    ['How can I help you', 'today?', 'Ask anything, paste an error, or drop a file. Vector works through code, debugging and engineering problems with you.'],
    code:    ['What should we', 'build?', 'Describe what you want and Vector will help you build it step by step.'],
    debug:   ['What is', 'breaking?', 'Paste the error or the stack trace and we will work out why it happens.'],
    review:  ['What should I', 'review?', 'Paste your code and Vector will look for bugs, slow parts and easy wins.'],
    diagram: ['What should we', 'map out?', 'Describe the system and Vector will sketch how the pieces fit together.'],
    image:   ['What should I', 'create?', 'Describe a picture and Vector will generate it for you.']
  }[m] || ['How can I help you','today?','Ask Vector anything.'];

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
/* chats on disk                                                       */
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
  if (window.innerWidth < 900) sidebar.classList.add('hidden');
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
  if (window.innerWidth < 900) sidebar.classList.add('hidden');
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
/* painting the thread                                                 */
/* ------------------------------------------------------------------ */
function addUser(text){
  const el = document.createElement('div');
  el.className = 'msg user';
  el.innerHTML = '<div class="bubble"></div>';
  el.querySelector('.bubble').textContent = text;
  messagesEl.appendChild(el);
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

  turns.forEach(m => {
    if (m.role === 'user') addUser(m.content);
    else addAI().innerHTML = plainRender(m.content);
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
    const res = await fetch('/api/providers');
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
