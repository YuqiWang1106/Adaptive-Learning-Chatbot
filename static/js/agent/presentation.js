export const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled', 'expired']);

export const RUN_STATUS_COPY = Object.freeze({
  queued: 'Preparing your learning session…',
  running: 'Working on your question…',
  waiting_for_clarification: 'I need one detail',
  waiting_for_approval: 'Your confirmation is required',
  resuming: 'Continuing your original task…',
  completed: 'Response complete',
  failed: 'This response could not be completed',
  cancelled: 'This task was stopped',
  expired: 'This task expired before completion',
});

const TOOL_LABELS = Object.freeze({
  'knowledge.retrieve_evidence': 'Course materials',
  knowledge_retrieve_evidence: 'Course materials',
  'adaptive.get_teaching_state': 'Learning progress',
  adaptive_get_teaching_state: 'Learning progress',
  'adaptive.list_learning_priorities': 'Learning priorities',
  adaptive_list_learning_priorities: 'Learning priorities',
  'probe.get_recent_diagnostics': 'Recent mastery reviews',
  probe_get_recent_diagnostics: 'Recent mastery reviews',
  'conversation.get_recent_learning_events': 'Recent conversation',
  conversation_get_recent_learning_events: 'Recent conversation',
  'learning.get_concept_map': 'Concept map',
  learning_get_concept_map: 'Concept map',
  'learning.get_goal_context': 'Learning goal',
  learning_get_goal_context: 'Learning goal',
  'assessment.get_recent_quizzes': 'Recent quizzes',
  assessment_get_recent_quizzes: 'Recent quizzes',
});

const EVIDENCE_LABELS = Object.freeze({
  curriculum_evidence: 'Course materials ready',
  teaching_context: 'Learning context ready',
  adaptive_state: 'Learning progress ready',
  probe_diagnostics: 'Recent mastery reviews ready',
  conversation_events: 'Recent conversation ready',
  concept_map: 'Concept map ready',
});

const SKILL_LABELS = Object.freeze({
  'Source-grounded explanation': 'Source-grounded explanation',
  source_grounded_explanation: 'Source-grounded explanation',
  'Worked example': 'Worked example',
  worked_example: 'Worked example',
  'Misconception repair': 'Misconception repair',
  misconception_repair: 'Misconception repair',
  'Retrieval practice': 'Retrieval practice',
  retrieval_practice: 'Retrieval practice',
  'Spaced review': 'Spaced review',
  spaced_review: 'Spaced review',
  'Assessment reflection': 'Assessment reflection',
  assessment_reflection: 'Assessment reflection',
});

const REVIEW_REASONS = Object.freeze({
  initial_calibration: 'A short review will help calibrate your starting point.',
  calibration: 'A short review will help calibrate your starting point.',
  forgetting: 'This concept is ready to be revisited.',
  chat_count: 'You have done enough new learning for a quick check-in.',
  time: 'It has been a while since this concept was reviewed.',
  review_due: 'This concept is ready for a brief evidence-based review.',
});

const APPROVAL_COPY = Object.freeze({
  'memory.propose': {
    summary: 'Save this learning preference?',
    positive: 'Save preference',
    will: 'This preference may guide future answers for this learning goal.',
    willNot: 'It will not change your mastery, and you can remove it later.',
  },
  'quiz.propose': {
    summary: 'Create a quiz draft?',
    positive: 'Create draft',
    will: 'A quiz draft will be created for you to review.',
    willNot: 'It will not start automatically or update your mastery.',
  },
  'review_plan.propose': {
    summary: 'Create a review plan draft?',
    positive: 'Create draft',
    will: 'A review plan draft will be created for you to review.',
    willNot: 'It will not schedule reminders automatically.',
  },
  'external.study_session.propose': {
    summary: 'Create a study session draft?',
    positive: 'Create draft',
    will: 'A study session draft will be created for you to review.',
    willNot: 'It will not add anything to an external calendar.',
  },
});

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));
}

export function sanitizeHtml(value) {
  const template = document.createElement('template');
  template.innerHTML = String(value ?? '');
  const allowed = new Set(['P', 'BR', 'STRONG', 'EM', 'B', 'I', 'UL', 'OL', 'LI', 'CODE', 'PRE', 'BLOCKQUOTE', 'H2', 'H3', 'H4', 'A']);
  Array.from(template.content.querySelectorAll('*')).forEach(element => {
    if (!allowed.has(element.tagName)) {
      element.replaceWith(document.createTextNode(element.textContent || ''));
      return;
    }
    const rawHref = element.tagName === 'A' ? element.getAttribute('href') || '' : '';
    Array.from(element.attributes).forEach(attribute => element.removeAttribute(attribute.name));
    if (element.tagName === 'A') {
      if (/^(https?:|mailto:|#|\/)/i.test(rawHref)) {
        element.setAttribute('href', rawHref);
        element.setAttribute('rel', 'noopener noreferrer');
      }
    }
  });
  return template.innerHTML;
}

export function runStatusCopy(status) {
  return RUN_STATUS_COPY[status] || 'Working on your question…';
}

export function toolLabel(value) {
  return TOOL_LABELS[String(value || '')] || 'Learning evidence';
}

export function skillLabel(value) {
  return SKILL_LABELS[String(value || '')] || 'Adaptive teaching';
}

export function activityForEvent(type, data = {}) {
  if (type === 'adaptive_context_loaded') return { key: 'context', copy: 'Learning context ready', tone: 'ok' };
  if (type === 'memory_loaded') return Number(data.total_prior_turns || 0) > 0
    ? { key: 'memory', copy: 'Recent conversation considered', tone: 'ok' }
    : null;
  if (type === 'plan_updated') return { key: 'plan', copy: 'Learning steps prepared', tone: 'ok' };
  if (type === 'skill_selected') return { key: 'skill', copy: 'Teaching approach selected', tone: 'ok' };
  if (type === 'tool_started') {
    const label = toolLabel(data.tool);
    return { key: `tool:${String(data.tool || 'unknown')}`, copy: `Checking ${label.toLowerCase()}…`, tone: 'running' };
  }
  if (type === 'tool_completed') {
    const label = toolLabel(data.tool);
    return {
      key: `tool:${String(data.tool || 'unknown')}`,
      copy: data.status === 'succeeded' ? `${label} ready` : `${label} could not be checked`,
      tone: data.status === 'succeeded' ? 'ok' : 'error',
    };
  }
  if (type === 'evidence_attached') {
    return {
      key: `evidence:${String(data.category || 'unknown')}`,
      copy: EVIDENCE_LABELS[String(data.category || '')] || 'Learning evidence ready',
      tone: 'ok',
    };
  }
  if (type === 'micro_check_offered') return { key: 'quick-practice', copy: 'Optional quick practice prepared', tone: 'ok' };
  if (type === 'run_failed') return { key: 'failed', copy: 'The response stopped before completion', tone: 'error' };
  return null;
}

export function reviewReason(offer = {}) {
  return REVIEW_REASONS[String(offer.trigger || offer.reason || '')]
    || 'This concept is ready for a brief evidence-based review.';
}

export function approvalCopy(payload = {}) {
  return APPROVAL_COPY[String(payload.action || '')] || {
    summary: 'Confirm this action?',
    positive: 'Confirm',
    will: 'The requested draft action will be completed.',
    willNot: 'Nothing else will be started automatically.',
  };
}

export function localExpiry(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export function friendlyError(_error, fallback = 'Something went wrong. Please try again.') {
  return fallback;
}
