import {
  TERMINAL_STATUSES,
  approvalCopy,
  escapeHtml,
  friendlyError,
  localExpiry,
  runStatusCopy,
  sanitizeHtml,
  skillLabel,
} from './presentation.js';
import { createRunState, hydrateRunState, reduceRunEvent } from './run_state.js';

export function createRunCards({
  thread,
  api,
  runUrl,
  onCancel,
  onResume,
  onTerminal,
  onMicroCheck,
  markWorkspaceActive,
}) {
  const states = new Map();

  function getState(run) {
    let state = states.get(run.run_id);
    if (!state) {
      state = createRunState(run);
      states.set(run.run_id, state);
    } else hydrateRunState(state, run);
    return state;
  }

  function cardFor(run) {
    let card = document.querySelector(`[data-agent-run="${CSS.escape(run.run_id)}"]`);
    if (card) return card;
    const row = document.createElement('div');
    row.className = 'agent-message agent-message-assistant';
    card = document.createElement('article');
    card.className = 'agent-run-card';
    card.dataset.agentRun = run.run_id;
    card.innerHTML = `
      <header class="agent-run-head">
        <span class="agent-run-state" role="status"><i class="bi bi-stars" aria-hidden="true"></i><span data-run-label>Preparing your learning session…</span></span>
        <button type="button" class="agent-cancel" data-run-cancel>Stop</button>
      </header>
      <div class="agent-run-body">
        <section class="agent-answer">
          <div data-run-interruption></div>
          <div class="agent-answer-pending" data-run-pending>
            <span class="agent-pulse" aria-hidden="true"></span>
            <div><strong>Preparing a validated response…</strong><small>Your answer will appear after its safety, personalization, and citation checks finish.</small></div>
          </div>
          <div data-run-final></div>
        </section>
        <details class="agent-process" data-run-process>
          <summary>How this answer was prepared</summary>
          <div class="agent-process-grid">
            <section><p class="agent-section-title">Learning steps</p><ul class="agent-plan" data-run-plan><li>Understand your question</li></ul></section>
            <section><p class="agent-section-title">Teaching approach</p><ul class="agent-evidence" data-run-skills><li>Adaptive teaching</li></ul></section>
            <section><p class="agent-section-title">Sources and learning context</p><ul class="agent-events" data-run-events><li class="agent-tool-running">Preparing learning context…</li></ul></section>
          </div>
        </details>
      </div>`;
    card.querySelector('[data-run-cancel]').addEventListener('click', () => onCancel(run.run_id));
    row.appendChild(card);
    thread.appendChild(row);
    markWorkspaceActive();
    return card;
  }

  function renderPlan(card, plan) {
    const items = Array.isArray(plan) && plan.length ? plan : [{ title: 'Understand your question', status: 'pending' }];
    const list = card.querySelector('[data-run-plan]');
    list.replaceChildren(...items.map(step => {
      const item = document.createElement('li');
      item.dataset.status = String(step.status || 'pending');
      item.textContent = String(step.title || 'Continue the learning task');
      return item;
    }));
  }

  function renderProcess(card, state) {
    renderPlan(card, state.run.plan);
    const skills = Array.isArray(state.run.selected_skills) ? state.run.selected_skills : [];
    const skillList = card.querySelector('[data-run-skills]');
    skillList.replaceChildren(...(skills.length ? skills : [{ name: 'Adaptive teaching' }]).map(skill => {
      const item = document.createElement('li');
      item.textContent = skillLabel(skill.name || skill.skill_id);
      return item;
    }));
    const activities = state.activities.length ? state.activities : [{ copy: 'Preparing learning context…', tone: 'running' }];
    const visibleActivities = activities.filter((activity, index, items) => items.findIndex(candidate => candidate.copy === activity.copy) === index);
    const activityList = card.querySelector('[data-run-events]');
    activityList.replaceChildren(...visibleActivities.map(activity => {
      const item = document.createElement('li');
      item.className = `agent-tool-${activity.tone}`;
      item.textContent = activity.copy;
      return item;
    }));
  }

  function focusDecision(surface, target) {
    window.requestAnimationFrame(() => {
      surface.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target?.focus({ preventScroll: true });
    });
  }

  function renderClarification(card, run, interruption) {
    const mount = card.querySelector('[data-run-interruption]');
    if (mount.dataset.interruptionId === interruption.interruption_id && mount.childElementCount) return;
    mount.dataset.interruptionId = interruption.interruption_id;
    const payload = interruption.payload || {};
    const options = Array.isArray(payload.options) ? payload.options : [];
    mount.innerHTML = `<section class="agent-interruption" tabindex="-1" aria-labelledby="clarification-${escapeHtml(interruption.interruption_id)}">
      <h3 id="clarification-${escapeHtml(interruption.interruption_id)}">I need one detail</h3>
      <p class="agent-decision-support">Your answer will let me continue the same task.</p>
      <p>${escapeHtml(payload.question || 'Please add the missing detail.')}</p>
      ${payload.why_needed ? `<p class="agent-decision-why">${escapeHtml(payload.why_needed)}</p>` : ''}
      ${options.length ? `<div class="agent-clarification-options">${options.map(option => `<button type="button" class="agent-button agent-button-secondary" data-clarification-option="${escapeHtml(option)}">${escapeHtml(option)}</button>`).join('')}</div>` : ''}
      <label class="agent-decision-input">Your answer<input data-clarification-answer maxlength="800" autocomplete="off"></label>
      <div class="agent-interruption-actions"><button type="button" class="agent-button agent-button-primary" data-clarification-submit>Continue</button>
      <button type="button" class="agent-button agent-button-secondary" data-clarification-stop>Stop this task</button></div>
      <p class="agent-decision-status" data-decision-status role="status" aria-live="polite"></p>
    </section>`;
    const surface = mount.querySelector('.agent-interruption');
    const answer = mount.querySelector('[data-clarification-answer]');
    const submit = mount.querySelector('[data-clarification-submit]');
    const controls = () => mount.querySelectorAll('button, input');
    let submitted = false;
    const resolve = async () => {
      const value = answer.value.trim();
      if (!value || submitted) return;
      submitted = true;
      controls().forEach(control => { control.disabled = true; });
      mount.querySelector('[data-decision-status]').textContent = 'Answer received. Continuing your task…';
      try {
        const resumed = await api.post(`${runUrl(run.run_id)}/clarifications/${encodeURIComponent(interruption.interruption_id)}`, { answer: value });
        onResume(run.run_id, card, resumed);
      } catch (error) {
        submitted = false;
        controls().forEach(control => { control.disabled = false; });
        mount.querySelector('[data-decision-status]').textContent = friendlyError(error, 'I could not send that answer. Please try again.');
        answer.focus();
      }
    };
    mount.querySelectorAll('[data-clarification-option]').forEach(button => button.addEventListener('click', () => {
      answer.value = button.dataset.clarificationOption || '';
      answer.focus();
    }));
    submit.addEventListener('click', resolve);
    answer.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); resolve(); }
    });
    mount.querySelector('[data-clarification-stop]').addEventListener('click', () => onCancel(run.run_id));
    focusDecision(surface, answer);
  }

  function renderApproval(card, run, interruption) {
    const mount = card.querySelector('[data-run-interruption]');
    if (mount.dataset.interruptionId === interruption.interruption_id && mount.childElementCount) return;
    mount.dataset.interruptionId = interruption.interruption_id;
    const payload = interruption.payload || {};
    const copy = approvalCopy(payload);
    const expires = localExpiry(interruption.expires_at);
    mount.innerHTML = `<section class="agent-interruption" tabindex="-1" aria-labelledby="approval-${escapeHtml(interruption.interruption_id)}">
      <h3 id="approval-${escapeHtml(interruption.interruption_id)}">Your confirmation is required</h3>
      <p class="agent-decision-support">${escapeHtml(copy.summary)}</p>
      <dl class="agent-approval-details">
        <div><dt>What will happen</dt><dd>${escapeHtml(copy.will)}</dd></div>
        <div><dt>What will not happen</dt><dd>${escapeHtml(copy.willNot)}</dd></div>
        ${expires ? `<div><dt>Available until</dt><dd>${escapeHtml(expires)}</dd></div>` : ''}
      </dl>
      <div class="agent-interruption-actions"><button type="button" class="agent-button agent-button-primary" data-approve>${escapeHtml(copy.positive)}</button>
      <button type="button" class="agent-button agent-button-secondary" data-reject>Not now</button></div>
      <p class="agent-decision-status" data-decision-status role="status" aria-live="polite"></p>
    </section>`;
    const surface = mount.querySelector('.agent-interruption');
    const controls = () => mount.querySelectorAll('button');
    let submitted = false;
    const resolve = async approved => {
      if (submitted) return;
      submitted = true;
      controls().forEach(control => { control.disabled = true; });
      mount.querySelector('[data-decision-status]').textContent = approved
        ? 'Confirmation received. Continuing your task…'
        : 'Choice received. Continuing your task…';
      try {
        const resumed = await api.post(`${runUrl(run.run_id)}/approvals/${encodeURIComponent(interruption.interruption_id)}`, { approved });
        onResume(run.run_id, card, resumed);
      } catch (error) {
        submitted = false;
        controls().forEach(control => { control.disabled = false; });
        mount.querySelector('[data-decision-status]').textContent = friendlyError(error, 'I could not save that choice. Please try again.');
      }
    };
    mount.querySelector('[data-approve]').addEventListener('click', () => resolve(true));
    mount.querySelector('[data-reject]').addEventListener('click', () => resolve(false));
    focusDecision(surface, mount.querySelector('[data-approve]'));
  }

  function renderInterruption(card, run) {
    const mount = card.querySelector('[data-run-interruption]');
    const interruption = run.interruption;
    document.body.classList.toggle('agent-decision-active', Boolean(interruption));
    if (!interruption) {
      mount.replaceChildren();
      delete mount.dataset.interruptionId;
      return;
    }
    if (interruption.kind === 'clarification') renderClarification(card, run, interruption);
    else renderApproval(card, run, interruption);
  }

  function renderFinal(card, run) {
    const mount = card.querySelector('[data-run-final]');
    if (!run.result?.answer) return;
    const citations = Array.isArray(run.result.citations) ? run.result.citations : [];
    mount.innerHTML = `
      <div class="agent-strategy">${escapeHtml(run.result.teaching_strategy || 'Adaptive teaching')}</div>
      <div class="agent-answer-final">${sanitizeHtml(run.result.answer)}</div>
      ${run.result.next_action ? `<p class="agent-next-action"><strong>Next step:</strong> ${escapeHtml(run.result.next_action)}</p>` : ''}
      ${citations.length ? `<div class="agent-citations"><p class="agent-section-title">Sources</p>${citations.map((citation, index) => `<details><summary>[${index + 1}] ${escapeHtml(citation.source)} · ${escapeHtml(citation.locator)}</summary><blockquote>${escapeHtml(citation.supporting_quote)}</blockquote></details>`).join('')}</div>` : ''}`;
    onMicroCheck(mount, run.result.micro_check);
  }

  function render(card, state) {
    const run = state.run;
    const terminal = TERMINAL_STATUSES.has(run.status);
    card.querySelector('[data-run-label]').textContent = runStatusCopy(run.status);
    card.querySelector('[data-run-cancel]').hidden = terminal;
    card.querySelector('[data-run-pending]').hidden = Boolean(run.result?.answer) || Boolean(run.interruption) || terminal;
    renderProcess(card, state);
    renderInterruption(card, run);
    renderFinal(card, run);
    if (terminal) onTerminal(run);
  }

  return {
    cardFor,
    applyRun(card, run) {
      const state = getState(run);
      render(card, state);
    },
    applyEvent(runId, card, type, data, eventId) {
      const state = states.get(runId) || getState({ run_id: runId, status: 'running', plan: [], selected_skills: [], result: {} });
      if (reduceRunEvent(state, type, data, eventId)) render(card, state);
    },
    after(runId) {
      return states.get(runId)?.lastEventId || 0;
    },
  };
}
