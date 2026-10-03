import { createApi } from './api.js';
import { createLearningCards } from './learning_cards.js';
import { friendlyError, sanitizeHtml, TERMINAL_STATUSES } from './presentation.js';
import { createRunCards } from './run_card.js';

export function initAdaptiveAgent() {
  const workspace = document.querySelector('[data-agent-workspace]');
  if (!workspace) return;

  const thread = document.getElementById('agent-thread');
  const form = document.getElementById('agent-question-form');
  const input = document.getElementById('agent-question');
  const submit = form.querySelector('[type="submit"]');
  const status = document.getElementById('agent-status');
  const composerGuard = document.getElementById('agent-composer-guard');
  const goalId = workspace.dataset.goalId;
  const runBaseUrl = workspace.dataset.runBaseUrl;
  const runUrl = runId => `${runBaseUrl}${encodeURIComponent(runId)}`;
  const activeKey = `learning_demo-agent-v2:${goalId}`;
  const csrfToken = form.querySelector('[name="csrfmiddlewaretoken"]')?.value || '';
  const api = createApi(csrfToken);
  const sources = new Map();
  let activeRunId = null;
  let historyCursor = workspace.dataset.historyCursor || '';

  const markWorkspaceActive = () => workspace.classList.add('agent-workspace-has-content');

  function setComposerBlocked(blocked) {
    input.disabled = blocked;
    submit.disabled = blocked;
    form.querySelectorAll('[data-material-modal-open]').forEach(button => { button.disabled = blocked; });
    composerGuard.hidden = !blocked;
    if (blocked) composerGuard.textContent = 'Finish, respond to, or stop the current task before starting another.';
  }

  function rememberActive(runId) {
    activeRunId = runId;
    localStorage.setItem(activeKey, runId);
    setComposerBlocked(true);
  }

  function forgetActive(runId) {
    if (activeRunId && activeRunId !== runId) return;
    activeRunId = null;
    localStorage.removeItem(activeKey);
    setComposerBlocked(false);
    if (!document.body.classList.contains('agent-decision-active')) input.focus({ preventScroll: true });
  }

  function messageRow(role, content, html = false) {
    const row = document.createElement('div');
    row.className = `agent-message agent-message-${role}`;
    const bubble = document.createElement('div');
    bubble.className = 'agent-message-bubble';
    if (html) bubble.innerHTML = sanitizeHtml(content);
    else bubble.textContent = content;
    row.appendChild(bubble);
    return row;
  }

  function addMessage(role, content, html = false, scroll = true) {
    const row = messageRow(role, content, html);
    thread.appendChild(row);
    if (role === 'user') markWorkspaceActive();
    if (scroll) row.scrollIntoView({ behavior: 'smooth', block: 'end' });
    return row;
  }

  function prependHistory(items) {
    if (!Array.isArray(items) || !items.length) return;
    const previousHeight = document.documentElement.scrollHeight;
    const fragment = document.createDocumentFragment();
    items.forEach(item => fragment.appendChild(messageRow(item.role === 'assistant' ? 'assistant' : 'user', item.content, item.role === 'assistant')));
    thread.insertBefore(fragment, thread.firstChild);
    window.scrollBy(0, Math.max(0, document.documentElement.scrollHeight - previousHeight));
  }

  let learningCards;
  const runCards = createRunCards({
    thread,
    api,
    runUrl,
    markWorkspaceActive,
    onCancel: cancelRun,
    onResume: (runId, card, run) => {
      runCards.applyRun(card, run);
      rememberActive(runId);
      connect(runId, card);
    },
    onTerminal: run => {
      document.body.classList.remove('agent-decision-active');
      forgetActive(run.run_id);
      if (!run.result?.micro_check) learningCards?.loadProbeOffer();
    },
    onMicroCheck: (mount, check) => learningCards?.renderMicroCheck(mount, check),
  });

  learningCards = createLearningCards({
    thread,
    api,
    microCheckActiveUrl: workspace.dataset.microCheckActiveUrl,
    microCheckBaseUrl: workspace.dataset.microCheckBaseUrl,
    probeOfferActiveUrl: workspace.dataset.probeOfferActiveUrl,
    probeOfferBaseUrl: workspace.dataset.probeOfferBaseUrl,
    trackUrl: workspace.dataset.trackUrl,
    markWorkspaceActive,
    onFeedbackRun: run => {
      const card = runCards.cardFor(run);
      runCards.applyRun(card, run);
      rememberActive(run.run_id);
      connect(run.run_id, card);
    },
  });

  async function getRun(runId) {
    return api.get(runUrl(runId));
  }

  function connect(runId, card) {
    sources.get(runId)?.close();
    const source = new EventSource(`${runUrl(runId)}/events?after=${runCards.after(runId)}`);
    sources.set(runId, source);
    const types = [
      'run_started', 'adaptive_context_loaded', 'memory_loaded', 'plan_updated', 'skill_selected',
      'tool_requested', 'tool_started', 'tool_completed', 'evidence_attached', 'clarification_requested',
      'approval_requested', 'approval_resolved', 'micro_check_offered', 'micro_check_blocked',
      'micro_check_answered', 'micro_check_skipped', 'micro_check_superseded', 'run_completed', 'run_failed',
    ];
    types.forEach(type => source.addEventListener(type, event => {
      let data = {};
      try { data = JSON.parse(event.data || '{}'); } catch (_error) { data = {}; }
      runCards.applyEvent(runId, card, type, data, event.lastEventId);
    }));
    const refresh = async (delay = 500) => {
      source.close();
      sources.delete(runId);
      try {
        const run = await getRun(runId);
        runCards.applyRun(card, run);
        if (['queued', 'running', 'resuming'].includes(run.status)) window.setTimeout(() => connect(runId, card), delay);
      } catch (error) {
        status.textContent = friendlyError(error, 'The connection was interrupted. Reloading this page is safe.');
      }
    };
    source.addEventListener('stream_end', () => refresh(500));
    source.onerror = () => refresh(1000);
  }

  async function cancelRun(runId) {
    try {
      const run = await api.post(`${runUrl(runId)}/cancel`, {});
      const card = runCards.cardFor(run);
      runCards.applyRun(card, run);
      sources.get(runId)?.close();
      sources.delete(runId);
      forgetActive(runId);
    } catch (error) {
      status.textContent = friendlyError(error, 'I could not stop this task. Please try again.');
    }
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (activeRunId) {
      status.textContent = 'Finish, respond to, or stop the current task before starting another.';
      return;
    }
    const question = input.value.trim();
    if (!question) return;
    input.value = '';
    input.style.height = 'auto';
    addMessage('user', question);
    setComposerBlocked(true);
    status.textContent = 'Preparing your learning session…';
    try {
      const run = await api.post(workspace.dataset.createUrl, { question, idempotency_key: crypto.randomUUID() });
      learningCards.markOpenPracticeMovedOn();
      const card = runCards.cardFor(run);
      runCards.applyRun(card, run);
      rememberActive(run.run_id);
      connect(run.run_id, card);
      status.textContent = '';
    } catch (error) {
      setComposerBlocked(false);
      status.textContent = friendlyError(error, 'The tutor could not start this response. Please try again.');
      input.focus();
    }
  });

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); }
  });
  document.querySelectorAll('[data-agent-suggestion]').forEach(button => button.addEventListener('click', () => {
    input.value = button.textContent.trim();
    input.dispatchEvent(new Event('input'));
    input.focus();
  }));

  const historyUrl = workspace.dataset.historyUrl;
  const historyLoad = document.querySelector('[data-history-load]');
  async function loadOlderHistory() {
    if (!historyUrl || !historyCursor || !historyLoad) return;
    historyLoad.disabled = true;
    const original = historyLoad.innerHTML;
    historyLoad.textContent = 'Loading earlier messages…';
    try {
      const payload = await api.get(`${historyUrl}&before=${encodeURIComponent(historyCursor)}`);
      prependHistory(payload.history || []);
      historyCursor = payload.next_cursor ? String(payload.next_cursor) : '';
      workspace.dataset.historyCursor = historyCursor;
      historyLoad.hidden = !historyCursor;
      if (!payload.history?.length) status.textContent = 'There are no earlier messages for this goal.';
    } catch (error) {
      status.textContent = friendlyError(error, 'Earlier messages could not be loaded. Please try again.');
    } finally {
      historyLoad.disabled = false;
      historyLoad.innerHTML = original;
    }
  }
  if (historyLoad) {
    historyLoad.hidden = !historyCursor;
    historyLoad.addEventListener('click', loadOlderHistory);
  }

  const memoryToggle = document.querySelector('[data-memory-toggle]');
  const memoryPanel = document.getElementById('agent-memory-panel');
  const memoryClose = document.querySelector('[data-memory-close]');
  const memoryForm = document.querySelector('[data-memory-form]');
  const memoryList = document.querySelector('[data-memory-list]');
  const memoryStatus = document.querySelector('[data-memory-status]');
  const memoriesUrl = workspace.dataset.memoriesUrl;

  function memoryValue(memory) {
    const value = memory?.value?.value;
    return value === undefined || value === null ? '' : String(value);
  }

  function renderMemories(memories) {
    if (!memoryList) return;
    if (!Array.isArray(memories) || !memories.length) {
      memoryList.innerHTML = '<p class="agent-memory-empty">No saved learning preferences for this goal yet.</p>';
      return;
    }
    memoryList.replaceChildren(...memories.map(memory => {
      const lifecycle = String(memory.lifecycle || 'proposed');
      const item = document.createElement('article');
      item.className = 'agent-memory-item';
      item.dataset.memoryId = memory.memory_id;
      const content = document.createElement('div');
      const badge = document.createElement('span');
      badge.className = 'agent-memory-lifecycle';
      badge.dataset.lifecycle = lifecycle;
      badge.dataset.memoryLifecycle = lifecycle;
      badge.textContent = lifecycle.replace(/_/g, ' ');
      const title = document.createElement('strong');
      title.textContent = String(memory.memory_key || '').replace(/_/g, ' ');
      const value = document.createElement('p');
      value.textContent = memoryValue(memory);
      content.append(badge, title, value);
      const actions = document.createElement('div');
      actions.className = 'agent-memory-actions';
      if (['proposed', 'conflicted'].includes(lifecycle)) actions.innerHTML += '<button type="button" class="agent-button agent-button-primary" data-memory-action="confirm">Confirm</button>';
      if (['active', 'conflicted'].includes(lifecycle)) actions.innerHTML += '<button type="button" class="agent-button agent-button-secondary" data-memory-action="revoke">Revoke</button>';
      item.append(content, actions);
      return item;
    }));
    memoryList.querySelectorAll('[data-memory-action]').forEach(button => button.addEventListener('click', async () => {
      const item = button.closest('[data-memory-id]');
      const action = button.dataset.memoryAction;
      item.querySelectorAll('button').forEach(control => { control.disabled = true; });
      memoryStatus.textContent = action === 'confirm' ? 'Confirming this preference…' : 'Removing this preference…';
      try {
        await api.post(`${memoriesUrl}/${encodeURIComponent(item.dataset.memoryId)}/${action}`, { idempotency_key: crypto.randomUUID() });
        await loadMemories();
        memoryStatus.textContent = action === 'confirm' ? 'Preference confirmed.' : 'Preference removed.';
      } catch (error) {
        memoryStatus.textContent = friendlyError(error, 'This preference could not be updated. Please try again.');
        item.querySelectorAll('button').forEach(control => { control.disabled = false; });
      }
    }));
  }

  async function loadMemories() {
    if (!memoriesUrl || !memoryList) return;
    memoryStatus.textContent = 'Loading learning preferences…';
    try {
      const payload = await api.get(memoriesUrl);
      renderMemories(payload.memories || []);
      memoryStatus.textContent = '';
    } catch (error) {
      memoryStatus.textContent = friendlyError(error, 'Learning preferences could not be loaded.');
    }
  }

  function setMemoryPanel(open) {
    if (!memoryPanel || !memoryToggle) return;
    memoryPanel.hidden = !open;
    memoryToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      loadMemories();
      memoryPanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }
  memoryToggle?.addEventListener('click', () => setMemoryPanel(memoryPanel?.hidden !== false));
  memoryClose?.addEventListener('click', () => setMemoryPanel(false));

  memoryForm?.addEventListener('submit', async event => {
    event.preventDefault();
    const [kind, memoryKey] = String(memoryForm.querySelector('[data-memory-key]')?.value || '').split('|', 2);
    const valueInput = memoryForm.querySelector('[data-memory-value]');
    const value = valueInput?.value.trim() || '';
    if (!kind || !memoryKey || !value) return;
    memoryForm.querySelectorAll('button, input, select').forEach(control => { control.disabled = true; });
    memoryStatus.textContent = 'Adding this preference for confirmation…';
    try {
      await api.post(workspace.dataset.memoryProposalUrl, { kind, memory_key: memoryKey, value_payload: { value }, idempotency_key: crypto.randomUUID() });
      valueInput.value = '';
      await loadMemories();
      memoryStatus.textContent = 'Preference added. Confirm it below before it becomes active.';
    } catch (error) {
      memoryStatus.textContent = friendlyError(error, 'This preference could not be added. Please try again.');
    } finally {
      memoryForm.querySelectorAll('button, input, select').forEach(control => { control.disabled = false; });
    }
  });

  document.querySelector('[data-conversation-clear]')?.addEventListener('click', async event => {
    const button = event.currentTarget;
    if (!window.confirm('Clear this goal’s chat history and conversation memory? Learning evidence and mastery records will be kept.')) return;
    button.disabled = true;
    status.textContent = 'Clearing this conversation…';
    try {
      await api.delete(workspace.dataset.conversationUrl);
      sources.forEach(source => source.close());
      sources.clear();
      localStorage.removeItem(activeKey);
      window.location.reload();
    } catch (error) {
      button.disabled = false;
      status.textContent = friendlyError(error, 'This conversation could not be cleared. Please try again.');
    }
  });

  try {
    const history = JSON.parse(document.getElementById('agent-initial-history')?.textContent || '[]');
    if (history.length) {
      markWorkspaceActive();
      history.forEach(item => addMessage(item.role === 'assistant' ? 'assistant' : 'user', item.content, item.role === 'assistant', false));
    } else addMessage('assistant', 'Tell me what you want to understand, practice, or review for this learning goal.', false);
  } catch (_error) {
    addMessage('assistant', 'Tell me what you want to understand, practice, or review.', false);
  }

  const activeRun = localStorage.getItem(activeKey);
  if (activeRun) {
    activeRunId = activeRun;
    setComposerBlocked(true);
    getRun(activeRun).then(run => {
      const card = runCards.cardFor(run);
      runCards.applyRun(card, run);
      if (!TERMINAL_STATUSES.has(run.status) && !['waiting_for_clarification', 'waiting_for_approval'].includes(run.status)) connect(run.run_id, card);
      else if (TERMINAL_STATUSES.has(run.status)) learningCards.loadDecisionCards();
    }).catch(() => {
      forgetActive(activeRun);
      learningCards.loadDecisionCards();
    });
  } else learningCards.loadDecisionCards();
}
