import { activityForEvent } from './presentation.js';

export function createRunState(run) {
  return {
    run: { ...run },
    lastEventId: 0,
    unsequencedEvents: new Set(),
    activities: [],
  };
}

export function hydrateRunState(state, run) {
  state.run = { ...state.run, ...run };
  if (!Object.prototype.hasOwnProperty.call(run, 'interruption')) delete state.run.interruption;
  return state;
}

export function reduceRunEvent(state, type, data = {}, eventId = '') {
  const sequence = Number(eventId || 0);
  if (sequence && sequence <= state.lastEventId) return false;
  if (sequence) state.lastEventId = sequence;
  else {
    const key = `${type}:${JSON.stringify(data)}`;
    if (state.unsequencedEvents.has(key)) return false;
    state.unsequencedEvents.add(key);
  }

  if (type === 'plan_updated' && Array.isArray(data.steps)) state.run.plan = data.steps;
  if (type === 'skill_selected') {
    const selected = Array.isArray(state.run.selected_skills) ? [...state.run.selected_skills] : [];
    if (!selected.some(skill => skill.name === data.name || skill.skill_id === data.skill_id)) selected.push(data);
    state.run.selected_skills = selected;
  }
  // Completion is not student-visible until the validated, persisted result is
  // fetched from the run endpoint. The SSE marker only tells us to refresh.
  if (type === 'run_failed') state.run.status = 'failed';
  if (type === 'clarification_requested') state.run.status = 'waiting_for_clarification';
  if (type === 'approval_requested') state.run.status = 'waiting_for_approval';
  if (type === 'approval_resolved') state.run.status = 'resuming';

  const activity = activityForEvent(type, data);
  if (activity) {
    const existing = state.activities.findIndex(item => item.key === activity.key);
    if (existing >= 0) state.activities.splice(existing, 1, activity);
    else state.activities.push(activity);
  }
  return true;
}
