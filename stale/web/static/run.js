// Tab switcher
const tabs = Array.from(document.querySelectorAll('.tab'));
const tabPanels = Array.from(document.querySelectorAll('.tab-panel'));
const tabsNav = document.querySelector('.tabs');
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

const runBackLink = document.querySelector('[data-run-back]');
if (runBackLink) {
  runBackLink.addEventListener('click', event => {
    if (window.history.length <= 1) return;
    event.preventDefault();
    window.history.back();
  });
}

function moveTabIndicator(btn) {
  if (!tabsNav || !btn) return;
  const navRect = tabsNav.getBoundingClientRect();
  const btnRect = btn.getBoundingClientRect();
  tabsNav.style.setProperty('--tab-indicator-x', `${btnRect.left - navRect.left}px`);
  tabsNav.style.setProperty('--tab-indicator-w', `${btnRect.width}px`);
}

function animatePanelChange(previousPanel, nextPanel, direction) {
  if (reducedMotion.matches || !nextPanel) return;

  if (previousPanel) {
    previousPanel.animate(
      [
        { opacity: 1, transform: 'translateY(0)' },
        { opacity: 0, transform: `translateY(${direction > 0 ? -8 : 8}px)` },
      ],
      { duration: 120, easing: 'cubic-bezier(0.23, 1, 0.32, 1)' },
    );
  }

  nextPanel.animate(
    [
      { opacity: 0, transform: `translateY(${direction > 0 ? 12 : -12}px)` },
      { opacity: 1, transform: 'translateY(0)' },
    ],
    { duration: 280, easing: 'cubic-bezier(0.23, 1, 0.32, 1)' },
  );
}

function activateTab(btn) {
  const nextPanel = document.getElementById('tab-' + btn.dataset.tab);
  if (!nextPanel) return;

  const previousBtn = tabs.find(tab => tab.classList.contains('active'));
  const previousPanel = tabPanels.find(panel => panel.classList.contains('active'));
  if (previousBtn === btn && previousPanel === nextPanel) {
    moveTabIndicator(btn);
    return;
  }

  const direction = Math.sign(tabs.indexOf(btn) - tabs.indexOf(previousBtn)) || 1;

  tabs.forEach(tab => {
    const selected = tab === btn;
    tab.classList.toggle('active', selected);
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });

  tabPanels.forEach(panel => panel.classList.toggle('active', panel === nextPanel));
  moveTabIndicator(btn);
  animatePanelChange(previousPanel, nextPanel, direction);
}

tabs.forEach((btn, index) => {
  btn.addEventListener('click', () => activateTab(btn));
  btn.addEventListener('keydown', event => {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
    event.preventDefault();
    const offset = event.key === 'ArrowRight' ? 1 : -1;
    const next = tabs[(index + offset + tabs.length) % tabs.length];
    next.focus();
    activateTab(next);
  });
});

requestAnimationFrame(() => moveTabIndicator(tabs.find(btn => btn.classList.contains('active'))));
window.addEventListener('resize', () => moveTabIndicator(tabs.find(btn => btn.classList.contains('active'))));

// Role chip → fill the free-text input. Don't auto-submit — let the user
// refine if they want ("backend engineer" → "backend engineer focused on
// real-time distributed systems") before hitting Find market gaps.
document.querySelectorAll('.role-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    const input = document.getElementById('role-text-input');
    if (input) {
      input.value = chip.dataset.label || chip.textContent.trim();
      input.focus();
      // Move caret to end so the user can keep typing a specialization
      input.selectionStart = input.selectionEnd = input.value.length;
    }
  });
});

// Live event stream (SSE) remains guarded for older pages that include a log.
// Current report pages keep the UI focused on Findings, Market-fit, and Topics.
const log = document.getElementById('event-log');
function append(msg, cls, source) {
  if (!log) return;
  const div = document.createElement('div');
  div.className = 'ev ' + (cls || '') + (source ? ' src-' + source : '');
  const ts = new Date().toLocaleTimeString();
  const srcTag = source ? '<span class="src">' + source + '</span>' : '';
  div.innerHTML = '<span class="t">' + ts + '</span>' + srcTag + msg;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}
function classify(type) {
  if (type === 'agent.thinking') return 'think';
  if (type.startsWith('agent.tool')) return 'tool';
  if (type === 'agent.message')  return 'msg';
  if (type.startsWith('session.status_')) return 'status';
  if (type === 'session.error') return 'error';
  return '';
}

if (log) {
  const es = new EventSource(`/run/${RUN_ID}/events.stream`);
  es.onmessage = (e) => {
    if (!e.data) return;
    let evt;
    try { evt = JSON.parse(e.data); } catch { return; }
    const cls = classify(evt.type || '');
    const src = evt._source || '';
    let line = '<strong>' + (evt.type || '?') + '</strong>';
    if (evt.type === 'agent.tool_use') {
      line += ' — ' + (evt.name || '?');
    } else if (evt.type === 'agent.message') {
      const txt = (evt.content || []).map(c => c.text || '').join(' ').slice(0, 240);
      if (txt) line += ' — ' + escapeHtml(txt);
    } else if (evt.type === 'session.error') {
      line += ' — ' + escapeHtml(JSON.stringify(evt).slice(0, 240));
    }
    append(line, cls, src);
  };
  es.addEventListener('done', (e) => {
    let data = {};
    try { data = JSON.parse(e.data); } catch {}
    append('<strong>stream closed</strong> (' + (data.status || 'done') + ')', 'status');
    es.close();
  });
  es.onerror = () => { /* network blip; browser auto-reconnects */ };
}

// Poll /state for lightweight status updates without interrupting browsing.
// New tab content will appear on the next manual navigation or refresh.
const initialAgents = INITIAL_AGENTS || {};
const initialScopeRendered = !!document.querySelector('.scope-panel');
let lastSeen = {
  stage1_done: !!(typeof INITIAL_STAGE1_DONE !== 'undefined' && INITIAL_STAGE1_DONE),
  stage2_started: !!(typeof INITIAL_STAGE2_STARTED !== 'undefined' && INITIAL_STAGE2_STARTED),
  scope_done: initialScopeRendered,
};

async function pollState() {
  try {
    const r = await fetch(`/run/${RUN_ID}/state`);
    if (!r.ok) return;
    const s = await r.json();
    const statusPill = document.getElementById('run-status');
    if (statusPill && s.status && statusPill.textContent !== s.status) {
      statusPill.className = 'status-pill status-' + s.status;
      statusPill.textContent = s.status;
    }
    lastSeen.stage1_done = !!s.stage1_done;
    lastSeen.stage2_started = !!s.stage2_started;
    lastSeen.scope_done = !!s.scope_done;
  } catch {}
  setTimeout(pollState, 5000);
}
if (INITIAL_STATUS !== 'done' && INITIAL_STATUS !== 'error') {
  setTimeout(pollState, 5000);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}
