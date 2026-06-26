// @ts-check
/**
 * Inline toast notification.
 *
 * Renders a transient banner into `#toast-container` and removes it
 * after a fixed duration. The container element is rendered into the
 * shell template and is always present at runtime; in tests it must
 * be created in the DOM before calling.
 *
 * Bridged onto window via index.html's module bootstrap so the ~270
 * existing call sites keep working without source changes.
 */

/**
 * @typedef {Object} ToastAction
 * @property {string} label - Button text (e.g. "Undo").
 * @property {() => void} fn - Click handler.
 */

/**
 * @typedef {Object} ToastOpts
 * @property {ToastAction} [action] - Optional action button. Bumps
 *   the auto-dismiss duration from 3.75s to 6s so the user has time
 *   to click.
 * @property {number} [duration] - Explicit auto-dismiss in ms; overrides
 *   the action/no-action default (e.g. 20000 for a recoverable Undo).
 */

/**
 * Show a transient toast.
 *
 * @param {string} msg - Message to display.
 * @param {true | "error" | "warning" | "success" | undefined | null} [type] -
 *   Severity. `true` and `"error"` both map to error styling;
 *   `"warning"` maps to warning; anything else ("success" included)
 *   is the default success/info styling.
 * @param {ToastOpts} [opts]
 */
export function toast(msg, type, opts) {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const el = document.createElement("div");
  const isError = type === true || type === "error";
  const isWarning = type === "warning";
  el.className =
    "toast" + (isError ? " error" : "") + (isWarning ? " warning" : "");

  const textSpan = document.createElement("span");
  textSpan.textContent = msg;
  el.appendChild(textSpan);

  if (opts && opts.action) {
    const btn = document.createElement("button");
    btn.className = "toast-action";
    btn.textContent = opts.action.label;
    btn.onclick = (e) => {
      e.stopPropagation();
      el.remove();
      opts.action?.fn();
    };
    el.appendChild(btn);
  }

  container.appendChild(el);
  requestAnimationFrame(() => el.classList.add("visible"));

  const duration = (opts && opts.duration) || (opts && opts.action ? 6000 : 3750);
  setTimeout(() => {
    el.classList.remove("visible");
    setTimeout(() => el.remove(), 300);
  }, duration);
}

/**
 * Render an error toast per the project's Error-Toast Policy: name the
 * ACTION, the REASON, and a RECOVERY hint — never a bare "Failed".
 *
 * `err.message` from `apiFetch` already carries the server's structured
 * reason (`body.error`) or `"HTTP <status>"`, so passing the caught error
 * surfaces the real cause instead of swallowing it.
 *
 * @param {string} action - what the user was trying to do, phrased to follow
 *   "Couldn't " — e.g. "create the album", "rename this person".
 * @param {unknown} [err] - the caught error / rejected value.
 * @param {string} [recovery="try again"] - short recovery hint.
 */
export function toastError(action, err, recovery = "try again") {
  const reason = (/** @type {any} */ (err) && /** @type {any} */ (err).message) || "unknown error";
  toast(`Couldn't ${action}: ${reason} — ${recovery}`, true);
}

/**
 * Legacy variant — used by the activity-log / albums / clip flows
 * that need an "Undo" button with a custom callback. Differs from
 * `toast()` in that the duration is configurable and the action
 * label is fixed to "Undo".
 *
 * Prefer `toast(msg, type, { action: { label, fn } })` for new code.
 *
 * @param {string} message
 * @param {number} [duration] - Auto-dismiss in ms. Default 3000.
 * @param {() => void} [onUndo] - Optional click handler. When set,
 *   renders an Undo button alongside the message.
 */
export function showToast(message, duration, onUndo) {
  duration = duration || 3000;
  const container = document.getElementById("toast-container");
  if (!container) return;

  const el = document.createElement("div");
  el.className = "toast";

  if (onUndo) {
    const body = document.createElement("span");
    body.className = "toast-body";
    const text = document.createElement("span");
    text.textContent = message;
    const btn = document.createElement("button");
    btn.className = "toast-undo";
    btn.textContent = "Undo";
    btn.onclick = () => {
      onUndo();
      el.classList.remove("visible");
      setTimeout(() => el.remove(), 300);
    };
    body.appendChild(text);
    body.appendChild(btn);
    el.appendChild(body);
  } else {
    el.textContent = message;
  }

  container.appendChild(el);
  requestAnimationFrame(() => el.classList.add("visible"));
  setTimeout(() => {
    el.classList.remove("visible");
    setTimeout(() => el.remove(), 300);
  }, duration);
}
