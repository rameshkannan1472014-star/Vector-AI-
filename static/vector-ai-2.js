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

const STYLE = 'Reply in short, friendly plain text with emojis. ' +
              'Never use markdown: no asterisks, no #, no code blocks, no bullet dashes. ' +
              'Explain it simply, like you are talking to a 12 year old.';

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
  writeLS(LS.notes, notes);
  paintMemoryBadge();
  toast('Saved to memory ✅');
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

function attachActions(msgEl, index){
  if (!msgEl) return;
  const old = msgEl.querySelector('.msg-actions');
  if (old) old.remove();
  const oldFollow = msgEl.querySelector('.followups');
  if (oldFollow) oldFollow.remove();

  const row = document.createElement('div');
  row.className = 'msg-actions';

  const actions = [
    { label: '⧉ Copy', run: () => copyAnswer(index) },
    { label: '↻ Try again', run: () => retryAt(index) },
    { label: '☆ Save to memory', run: () => saveAnswer(index) }
  ];
  actions.forEach(a => {
    const b = document.createElement('button');
    b.className = 'act';
    b.textContent = a.label;
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
    const res = await fetch('/api/projects');
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
    const res = await fetch('/api/projects', {
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
  attachments.forEach(a => {
    if (a.kind === 'text') parts.push('File ' + a.name + ':\n' + a.text);
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
  const body = addAI();
  body.innerHTML = '<span class="caret"></span>';

  let answer = null;
  let failed = false;

  try {
    const res = await fetch('/api/chat', {
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

  if (!failed){
    turns.push({ role: 'assistant', content: String(answer) });
    body.innerHTML = plainRender(answer);
    syncChat();
  } else {
    body.innerHTML = plainRender(answer);
  }

  attachActions(body.parentElement, failed ? -1 : turns.length - 1);

  attachments = [];
  paintAttachments();

  busy = false;
  autogrow();
  scrollBottom();
  input.focus();
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
    const res = await fetch('/api/me');
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

function authAction(){
  if (me) signOut();
  else openGate();
}

async function signOut(){
  try { await fetch('/auth/logout', { method: 'POST' }); } catch (e) {}
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

function openLive(){
  closeAcctMenu();
  closeDrawer();
  liveOn = true;
  $('liveAnswer').textContent = '';
  $('live').classList.add('open');
  $('liveStatus').textContent = SR
    ? 'Tap the circle and start talking.'
    : 'This browser has no voice input, so type below and Vector will answer out loud.';
}

function closeLive(){
  liveOn = false;
  $('live').classList.remove('open');
  stopRec();
  if ('speechSynthesis' in window){
    try { speechSynthesis.cancel(); } catch (e) {}
  }
}

function setOrb(on){ $('liveOrb').classList.toggle('rec', !!on); }

function liveToggle(){
  if (recState === 'listening'){
    stopRec();
    $('liveStatus').textContent = 'Paused. Tap the circle to talk again.';
    return;
  }
  startRec();
}

function startRec(){
  if (!SR){
    $('liveStatus').textContent = 'Type your question below and Vector will answer out loud.';
    $('liveText').focus();
    return;
  }
  try { rec = new SR(); }
  catch (e) { $('liveStatus').textContent = 'I could not start the microphone.'; return; }

  rec.lang = 'en-IN';
  rec.interimResults = true;
  rec.continuous = false;

  rec.onstart = () => { recState = 'listening'; setOrb(true); $('liveStatus').textContent = 'Listening…'; };

  rec.onresult = ev => {
    let said = '';
    for (let i = 0; i < ev.results.length; i++) said += ev.results[i][0].transcript;
    $('liveStatus').textContent = said;
    const last = ev.results[ev.results.length - 1];
    if (last && last.isFinal){
      stopRec();
      liveSend(said);
    }
  };

  rec.onerror = ev => {
    setOrb(false);
    recState = 'idle';
    $('liveStatus').textContent = ev.error === 'not-allowed'
      ? 'Microphone is blocked. Allow it in the browser, or type below.'
      : 'I did not catch that — tap the circle and try again.';
  };

  rec.onend = () => { setOrb(false); if (recState === 'listening') recState = 'idle'; };

  try { rec.start(); }
  catch (e) { $('liveStatus').textContent = 'Tap the circle once more to start listening.'; }
}

function stopRec(){
  if (rec){
    try { rec.onend = null; rec.stop(); } catch (e) {}
    rec = null;
  }
  recState = 'idle';
  setOrb(false);
}

async function liveSend(text){
  const question = String(text || '').trim();
  if (!question) return;

  $('liveText').value = '';
  $('liveStatus').textContent = 'Thinking…';
  $('liveAnswer').textContent = '';

  ensureChat(question);
  turns.push({ role: 'user', content: question });
  syncChat();

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: STYLE + '\n\nAnswer this out loud, so keep it to two or three short sentences: ' + question,
        provider: provider,
        history: [],
        memory: memoryPayload(),
        workspace_context: contextPayload(),
        images: []
      })
    });

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

  loadAuth();
  loadProviders();
  syncSide();

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
