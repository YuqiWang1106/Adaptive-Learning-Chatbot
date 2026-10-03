import { escapeHtml, friendlyError, reviewReason } from './presentation.js';

export function createLearningCards({
  thread,
  api,
  microCheckActiveUrl,
  microCheckBaseUrl,
  probeOfferActiveUrl,
  probeOfferBaseUrl,
  trackUrl,
  onFeedbackRun,
  markWorkspaceActive,
}) {
  const microUrl = (id, action) => `${microCheckBaseUrl}${encodeURIComponent(id)}/${action}`;

  function renderMicroCheck(mount, check) {
    if (!mount || !check?.check_id) return;
    let card = mount.querySelector(`[data-micro-check="${CSS.escape(check.check_id)}"]`);
    if (!card) {
      card = document.createElement('section');
      card.className = 'agent-micro-check';
      card.dataset.microCheck = check.check_id;
      mount.appendChild(card);
    }
    card.dataset.status = check.status;
    const isOpen = check.status === 'offered';
    const options = Array.isArray(check.options) ? check.options : [];
    const responseControl = check.response_format === 'single_choice'
      ? `<fieldset class="agent-micro-options"><legend class="visually-hidden">Choose one answer</legend>${options.map(option => `<label><input type="radio" name="micro-${escapeHtml(check.check_id)}" value="${escapeHtml(option)}"><span>${escapeHtml(option)}</span></label>`).join('')}</fieldset>`
      : '<label class="agent-micro-input-label">Your answer<input class="agent-micro-input" data-micro-answer maxlength="4000" autocomplete="off"></label>';
    const statusCopy = {
      skipped: 'Skipped — you can continue learning.',
      superseded: 'Moved on',
      expired: 'This optional practice has expired.',
      answered: 'Answer received.',
    }[check.status] || '';
    card.innerHTML = `<div class="agent-micro-heading"><span>Quick practice · Optional</span><small>Does not update your long-term mastery</small></div>
      <p class="agent-micro-prompt">${escapeHtml(check.prompt)}</p>
      ${isOpen ? responseControl : ''}
      ${isOpen ? '<div class="agent-micro-actions"><button type="button" class="agent-button agent-button-primary" data-micro-submit>Check answer</button><button type="button" class="agent-button agent-button-secondary" data-micro-skip>Skip</button></div>' : ''}
      <p class="agent-micro-note" data-micro-status role="status" aria-live="polite">${escapeHtml(statusCopy || 'You can keep chatting without answering.')}</p>`;
    if (!isOpen) return;
    card.querySelector('[data-micro-submit]').addEventListener('click', async () => {
      const selected = card.querySelector('input[type="radio"]:checked');
      const answer = selected?.value || card.querySelector('[data-micro-answer]')?.value.trim() || '';
      const status = card.querySelector('[data-micro-status]');
      if (!answer) { status.textContent = 'Enter or select an answer first.'; return; }
      card.querySelectorAll('button, input').forEach(element => { element.disabled = true; });
      status.textContent = 'Checking this answer…';
      try {
        const payload = await api.post(microUrl(check.check_id, 'answer'), { answer, idempotency_key: crypto.randomUUID() });
        renderMicroCheck(mount, payload.micro_check);
        if (payload.run) onFeedbackRun(payload.run);
      } catch (error) {
        card.querySelectorAll('button, input').forEach(element => { element.disabled = false; });
        status.textContent = friendlyError(error, 'I could not grade this answer reliably.');
      }
    });
    card.querySelector('[data-micro-skip]').addEventListener('click', async () => {
      card.querySelectorAll('button, input').forEach(element => { element.disabled = true; });
      try {
        const payload = await api.post(microUrl(check.check_id, 'skip'), {});
        renderMicroCheck(mount, payload.micro_check);
      } catch (error) {
        card.querySelector('[data-micro-status]').textContent = friendlyError(error, 'I could not skip this practice. Please try again.');
      }
    });
  }

  function renderDetachedMicroCheck(check) {
    if (!check?.check_id || document.querySelector(`[data-micro-check="${CSS.escape(check.check_id)}"]`)) return;
    const row = document.createElement('div');
    row.className = 'agent-message agent-message-assistant';
    const shell = document.createElement('div');
    shell.className = 'agent-standalone-learning-check';
    row.appendChild(shell);
    thread.appendChild(row);
    renderMicroCheck(shell, check);
    markWorkspaceActive();
  }

  function renderProbeOffer(offer) {
    if (!offer?.offer_id || document.querySelector('[data-micro-check][data-status="offered"]')) return;
    let card = document.querySelector(`[data-probe-offer="${CSS.escape(offer.offer_id)}"]`);
    if (!card) {
      const row = document.createElement('div');
      row.className = 'agent-message agent-message-assistant';
      card = document.createElement('section');
      card.className = 'agent-probe-offer';
      card.dataset.probeOffer = offer.offer_id;
      row.appendChild(card);
      thread.appendChild(row);
      markWorkspaceActive();
    }
    card.dataset.status = offer.status;
    const target = offer.target || {};
    const ready = offer.status === 'ready';
    const pending = offer.status === 'pending';
    const preparing = ['accepted', 'generating'].includes(offer.status);
    const dimension = String(target.dimension || '').replace(/_/g, ' ');
    card.innerHTML = `<div class="agent-micro-heading"><span>Mastery review · Optional</span><small>${escapeHtml(offer.estimated_minutes || '2–3')} minutes</small></div>
      <p class="agent-review-disclosure">A reliable result may update your learning progress.</p>
      <dl class="agent-review-meta"><div><dt>Concept</dt><dd>${escapeHtml(target.concept_label || 'Current learning goal')}</dd></div>${dimension ? `<div><dt>Dimension</dt><dd>${escapeHtml(dimension)}</dd></div>` : ''}</dl>
      <p class="agent-probe-reason">${escapeHtml(reviewReason(offer))}</p>
      ${pending ? '<div class="agent-micro-actions"><button type="button" class="agent-button agent-button-primary" data-probe-action="accept">Start review</button><button type="button" class="agent-button agent-button-secondary" data-probe-action="snooze">Remind me later</button><button type="button" class="agent-button agent-button-secondary" data-probe-action="dismiss">Dismiss</button></div>' : ''}
      ${ready ? `<a class="agent-button agent-button-primary agent-probe-link" href="${escapeHtml(trackUrl)}">Open mastery review</a>` : ''}
      <p class="agent-micro-note" data-probe-status role="status" aria-live="polite">${preparing ? 'Preparing your mastery review…' : offer.status === 'snoozed' ? 'We will remind you later.' : 'Optional — your chat remains available.'}</p>`;
    if (preparing) {
      window.setTimeout(loadProbeOffer, 1000);
      return;
    }
    if (!pending) return;
    card.querySelectorAll('[data-probe-action]').forEach(button => button.addEventListener('click', async () => {
      const action = button.dataset.probeAction;
      card.querySelectorAll('button').forEach(control => { control.disabled = true; });
      card.querySelector('[data-probe-status]').textContent = action === 'accept' ? 'Preparing your mastery review…' : 'Saving your choice…';
      try {
        const payload = await api.post(`${probeOfferBaseUrl}${encodeURIComponent(offer.offer_id)}/${action}`, {});
        if (['snooze', 'dismiss'].includes(action)) card.closest('.agent-message')?.remove();
        else renderProbeOffer(payload.probe_offer);
      } catch (error) {
        card.querySelectorAll('button').forEach(control => { control.disabled = false; });
        card.querySelector('[data-probe-status]').textContent = friendlyError(error, 'I could not update this review. Please try again.');
      }
    }));
  }

  async function loadProbeOffer() {
    if (!probeOfferActiveUrl || document.querySelector('[data-micro-check][data-status="offered"]')) return;
    try {
      const payload = await api.get(probeOfferActiveUrl);
      renderProbeOffer(payload?.probe_offer);
    } catch (_error) { /* Optional offers never block chat. */ }
  }

  async function loadDecisionCards() {
    if (microCheckActiveUrl) {
      try {
        const payload = await api.get(microCheckActiveUrl);
        if (payload?.micro_check) { renderDetachedMicroCheck(payload.micro_check); return; }
      } catch (_error) { /* Optional cards never block chat. */ }
    }
    loadProbeOffer();
  }

  function markOpenPracticeMovedOn() {
    document.querySelectorAll('[data-micro-check][data-status="offered"]').forEach(card => {
      card.dataset.status = 'superseded';
      const status = card.querySelector('[data-micro-status]');
      if (status) status.textContent = 'Moved on';
      card.querySelectorAll('button, input').forEach(element => { element.disabled = true; });
    });
  }

  return { renderMicroCheck, renderProbeOffer, loadProbeOffer, loadDecisionCards, markOpenPracticeMovedOn };
}
