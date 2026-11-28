/**
 * Pure step and scrubber logic for `#/replay`. No DOM, no timers.
 * Step 0 is the initial state; step N (the last) is after the final action.
 */

export const PLAY_INTERVAL_MS = 900;

/**
 * @param {number} total number of frames (actions + 1)
 * @returns {{ index: number, total: number, playing: boolean }}
 */
export function initialPlayer(total) {
  return { index: 0, total: Math.max(1, total | 0), playing: false };
}

/**
 * @param {number} index
 * @param {number} total
 */
export function clampStep(index, total) {
  const n = Number(index);
  if (!Number.isFinite(n)) return 0;
  return Math.min(Math.max(0, Math.trunc(n)), Math.max(0, total - 1));
}

/**
 * Frame indices where a turn begins. Action i (0-based) runs on frame i,
 * so a turn whose first action is i starts at frame i. Frame 0 is always one.
 * @param {Array<{ round: number, turnIndex: number }>} steps
 * @returns {number[]}
 */
export function turnStarts(steps) {
  const out = [0];
  let key = "";
  steps.forEach((step, i) => {
    const next = `${step.round}:${step.turnIndex}`;
    if (next !== key && i > 0) out.push(i);
    key = next;
  });
  return out;
}

/**
 * @param {{ index: number, total: number, playing: boolean }} state
 * @param {{ type: string, value?: number }} cmd
 * @param {number[]} [turns] from turnStarts
 */
export function reducePlayer(state, cmd, turns = []) {
  const last = state.total - 1;
  const at = (index, playing = false) => ({ ...state, index: clampStep(index, state.total), playing });
  switch (cmd && cmd.type) {
    case "first":
      return at(0);
    case "last":
      return at(last);
    case "prev":
      return at(state.index - 1);
    case "next":
      return at(state.index + 1);
    case "seek":
      return at(cmd.value);
    case "prevTurn": {
      const before = turns.filter((t) => t < state.index);
      return at(before.length ? before[before.length - 1] : 0);
    }
    case "nextTurn": {
      const after = turns.find((t) => t > state.index);
      return at(after === undefined ? last : after);
    }
    case "toggle":
      if (state.playing) return { ...state, playing: false };
      return { ...state, index: state.index >= last ? 0 : state.index, playing: last > 0 };
    case "pause":
      return { ...state, playing: false };
    case "tick":
      if (!state.playing) return state;
      if (state.index >= last) return { ...state, playing: false };
      return { ...state, index: state.index + 1, playing: state.index + 1 < last };
    default:
      return state;
  }
}

/**
 * Keyboard shortcuts. Returns null for keys the viewer does not own.
 * @param {string} key
 * @param {{ shiftKey?: boolean }} [mods]
 * @returns {{ type: string } | null}
 */
export function keyCommand(key, mods = {}) {
  if (key === "ArrowLeft") return { type: mods.shiftKey ? "prevTurn" : "prev" };
  if (key === "ArrowRight") return { type: mods.shiftKey ? "nextTurn" : "next" };
  if (key === "Home") return { type: "first" };
  if (key === "End") return { type: "last" };
  if (key === " " || key === "k") return { type: "toggle" };
  return null;
}

/**
 * Position text for the scrubber.
 * @param {{ frames: Array<{ round: number }>, steps: Array<{ round: number, turnIndex: number, actorName: string }> }} replay
 * @param {number} index
 */
export function stepLabel(replay, index) {
  const total = replay.steps.length;
  if (index <= 0) return `Start · round ${replay.frames[0].round} · step 0 of ${total}`;
  const step = replay.steps[index - 1];
  return `Round ${step.round} · turn ${step.turnIndex + 1} · step ${index} of ${total}`;
}
