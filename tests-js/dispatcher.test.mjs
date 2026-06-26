// @ts-check
/**
 * Tests for the _bppDispatch global click dispatcher and its helpers.
 *
 * Loads globals.js via Function() so the dispatcher is wired onto
 * the jsdom `document`.  Each test uses fresh DOM elements.
 */

import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import { readFileSync } from "fs";

const globalsSource = readFileSync("bpp/web/static/js/globals.js", "utf8");

// Load globals ONCE — re-running each test would accumulate event listeners
// and each click would fire the handler N times where N = test count.
beforeAll(() => {
  // eslint-disable-next-line no-new-func
  new Function("window", "document", globalsSource)(window, document);
});

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(() => {
  // Clean up per-test window stubs
  for (const key of Object.keys(window)) {
    if (key.startsWith("_test")) delete (/** @type {any} */ (window)[key]);
  }
});

// ── Arg coercion (tested via dispatcher observable behavior) ─────────────────

describe("arg coercion via dispatcher", () => {
  test("numeric string data-arg becomes a JS number", () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testNumArg = spy;
    const btn = document.createElement("button");
    btn.dataset.action = "_testNumArg";
    btn.dataset.arg0 = "42";
    btn.dataset.arg1 = "-7";
    btn.dataset.arg2 = "3.14";
    document.body.appendChild(btn);
    btn.click();
    expect(spy).toHaveBeenCalledWith(42, -7, 3.14);
  });

  test("boolean string data-arg becomes a JS boolean", () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testBoolArg = spy;
    const btn = document.createElement("button");
    btn.dataset.action = "_testBoolArg";
    btn.dataset.arg0 = "true";
    btn.dataset.arg1 = "false";
    document.body.appendChild(btn);
    btn.click();
    expect(spy).toHaveBeenCalledWith(true, false);
  });

  test("non-numeric string data-arg stays a string", () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testStrArg = spy;
    const btn = document.createElement("button");
    btn.dataset.action = "_testStrArg";
    btn.dataset.arg0 = "hello";
    btn.dataset.arg1 = "42abc";
    document.body.appendChild(btn);
    btn.click();
    expect(spy).toHaveBeenCalledWith("hello", "42abc");
  });

  test("no data-arg attributes → function called with no args", () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testNoArgs = spy;
    const btn = document.createElement("button");
    btn.dataset.action = "_testNoArgs";
    document.body.appendChild(btn);
    btn.click();
    expect(spy).toHaveBeenCalledWith();
  });

  test("gap in arg numbering stops collection at gap", () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testGapArgs = spy;
    const btn = document.createElement("button");
    btn.dataset.action = "_testGapArgs";
    btn.dataset.arg0 = "a";
    // arg1 intentionally missing
    btn.dataset.arg2 = "c";
    document.body.appendChild(btn);
    btn.click();
    // Only arg0 is collected; arg2 is beyond the gap
    expect(spy).toHaveBeenCalledWith("a");
  });
});

// ── Click dispatcher ─────────────────────────────────────────────────────────

describe("_bppDispatch (click)", () => {
  test("calls window[data-action] when clicked", async () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testAction = spy;
    const btn = document.createElement("button");
    btn.dataset.action = "_testAction";
    document.body.appendChild(btn);
    btn.click();
    expect(spy).toHaveBeenCalledOnce();
  });

  test("passes data-arg* to the function", async () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testWithArgs = spy;
    const btn = document.createElement("button");
    btn.dataset.action = "_testWithArgs";
    btn.dataset.arg0 = "hello";
    btn.dataset.arg1 = "99";
    document.body.appendChild(btn);
    btn.click();
    expect(spy).toHaveBeenCalledWith("hello", 99);
  });

  test("data-pass-event sends event as first arg", async () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testPassEvent = spy;
    const btn = document.createElement("button");
    btn.dataset.action = "_testPassEvent";
    btn.dataset.passEvent = "true";
    btn.dataset.arg0 = "42";
    document.body.appendChild(btn);
    btn.click();
    expect(spy).toHaveBeenCalledOnce();
    const [firstArg, secondArg] = spy.mock.calls[0];
    expect(firstArg).toBeInstanceOf(MouseEvent);
    expect(secondArg).toBe(42);
  });

  test("warns and no-ops for unknown action", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const btn = document.createElement("button");
    btn.dataset.action = "_noSuchFunctionDefinedAnywhere";
    document.body.appendChild(btn);
    btn.click();
    expect(warn).toHaveBeenCalledWith(
      "bpp dispatch: unknown action:",
      "_noSuchFunctionDefinedAnywhere"
    );
    warn.mockRestore();
  });

  test("skips elements inside .ctx-menu", async () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testCtxSkip = spy;
    const menu = document.createElement("div");
    menu.className = "ctx-menu";
    const item = document.createElement("div");
    item.dataset.action = "_testCtxSkip";
    menu.appendChild(item);
    document.body.appendChild(menu);
    item.click();
    expect(spy).not.toHaveBeenCalled();
  });

  test("data-stop-propagation stops the event", () => {
    const btn = document.createElement("button");
    btn.dataset.action = "_bppReload"; // defined in globals
    btn.dataset.stopPropagation = "true";
    document.body.appendChild(btn);
    const parentSpy = vi.fn();
    document.body.addEventListener("click", parentSpy);
    btn.click();
    // With capture=true on dispatcher + stopPropagation, parent listener
    // (bubble phase) should not fire
    expect(parentSpy).not.toHaveBeenCalled();
    document.body.removeEventListener("click", parentSpy);
  });

  test("sets this to the element when calling the function", () => {
    let capturedThis = null;
    /** @type {any} */ (window)._testThis = function () {
      capturedThis = this;
    };
    const btn = document.createElement("button");
    btn.dataset.action = "_testThis";
    document.body.appendChild(btn);
    btn.click();
    expect(capturedThis).toBe(btn);
  });
});

// ── _bppBackdropClose ────────────────────────────────────────────────────────

describe("_bppBackdropClose", () => {
  test("calls backdrop-fn when target is the overlay itself", () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testClose = spy;
    const overlay = document.createElement("div");
    overlay.dataset.backdropFn = "_testClose";
    document.body.appendChild(overlay);

    // Simulate click where e.target === overlay
    const handler = /** @type {any} */ (window)._bppBackdropClose.bind(overlay);
    handler({ target: overlay });
    expect(spy).toHaveBeenCalledOnce();
  });

  test("does NOT call fn when target is a child element", () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testCloseChild = spy;
    const overlay = document.createElement("div");
    overlay.dataset.backdropFn = "_testCloseChild";
    const child = document.createElement("button");
    overlay.appendChild(child);

    const handler = /** @type {any} */ (window)._bppBackdropClose.bind(overlay);
    handler({ target: child });
    expect(spy).not.toHaveBeenCalled();
  });

  test("passes data-backdrop-arg as coerced value", () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testCloseArg = spy;
    const overlay = document.createElement("div");
    overlay.dataset.backdropFn = "_testCloseArg";
    overlay.dataset.backdropArg = "false";

    const handler = /** @type {any} */ (window)._bppBackdropClose.bind(overlay);
    handler({ target: overlay });
    expect(spy).toHaveBeenCalledWith(false);
  });
});

// ── T0.1: dispatcher must prefer the action registry over window[name] ──

describe("dispatcher registry-first lookup (T0.1)", () => {
  // The action-registry's purpose is to replace the string-lookup-against-
  // window pattern. The dispatcher reads window.__bppActionRegistry and
  // calls .get() before falling back to window[name]. These tests pin
  // both branches: registry-hit AND registry-miss.

  beforeEach(() => {
    // Fresh registry per test so handlers don't leak across tests.
    /** @type {any} */ (window).__bppActionRegistry = new Map();
  });

  afterEach(() => {
    delete (/** @type {any} */ (window).__bppActionRegistry);
  });

  test("registered handler is called instead of the window global of the same name", () => {
    const fromRegistry = vi.fn();
    const fromWindow = vi.fn();
    /** @type {any} */ (window).__bppActionRegistry.set("doThing", fromRegistry);
    /** @type {any} */ (window)._testRegistryDoThing = fromWindow;

    const btn = document.createElement("button");
    btn.dataset.action = "doThing";
    document.body.appendChild(btn);
    btn.click();

    expect(fromRegistry).toHaveBeenCalledTimes(1);
    expect(fromWindow).not.toHaveBeenCalled();

    delete (/** @type {any} */ (window)._testRegistryDoThing);
  });

  test("falls back to window[name] when the registry has no entry for the action", () => {
    const fromWindow = vi.fn();
    /** @type {any} */ (window)._testFallbackHandler = fromWindow;
    // Registry intentionally empty for this lookup.

    const btn = document.createElement("button");
    btn.dataset.action = "_testFallbackHandler";
    document.body.appendChild(btn);
    btn.click();

    expect(fromWindow).toHaveBeenCalledTimes(1);

    delete (/** @type {any} */ (window)._testFallbackHandler);
  });

  test("data-pass-event + data-arg0 still work with registry-resolved handlers", () => {
    const spy = vi.fn();
    /** @type {any} */ (window).__bppActionRegistry.set("handler", spy);

    const btn = document.createElement("button");
    btn.dataset.action = "handler";
    btn.dataset.passEvent = "";
    btn.dataset.arg0 = "42";
    document.body.appendChild(btn);
    btn.click();

    expect(spy).toHaveBeenCalledTimes(1);
    const args = spy.mock.calls[0];
    // First arg is the event (data-pass-event), second is coerced number.
    expect(args[0]).toBeInstanceOf(Event);
    expect(args[1]).toBe(42);
  });

  test("dispatcher handles a registry that's not yet defined (defensive path)", () => {
    // Simulate the pre-T0.1 production state where __bppActionRegistry
    // didn't exist. The window[name] fallback must still work.
    delete (/** @type {any} */ (window).__bppActionRegistry);
    const fromWindow = vi.fn();
    /** @type {any} */ (window)._testNoRegistry = fromWindow;

    const btn = document.createElement("button");
    btn.dataset.action = "_testNoRegistry";
    document.body.appendChild(btn);
    btn.click();

    expect(fromWindow).toHaveBeenCalledTimes(1);

    delete (/** @type {any} */ (window)._testNoRegistry);
    // Restore the registry for the afterEach guard.
    /** @type {any} */ (window).__bppActionRegistry = new Map();
  });
});

// ── Bug #3 (UAT 2026-06-01): dispatchers must not throw on non-Element targets ──
//
// The dispatcher pattern reads `e.target.closest(...)` to find a
// data-action ancestor. If e.target is a Text node, the document
// itself, or a window/event-emitter target, .closest is undefined
// and the dispatcher throws TypeError. Today's UAT saw a spam of
// 'globals.js?v=...: Uncaught TypeError: e.target.closest is not a
// function' toasts after filtering the photo grid — every dispatcher
// (click/keydown/input/change/mousedown/mouseup/mouseleave/touchstart/
// touchend/contextmenu/dblclick/pointerdown) is vulnerable. The fix
// adds a `typeof closest === 'function'` guard before calling.
//
// Note: jsdom dispatches with HTMLDocument as target when `target` is
// set to `document`. HTMLDocument DOES have .closest in jsdom (it's
// inherited from Document), so we simulate the real-world case by
// dispatching directly on a Text node whose `closest` is undefined.

describe("dispatcher resilience: non-Element targets must not throw", () => {
  test("click with no closest() method (Text node target) is a silent no-op", () => {
    // Create a real Text node — its prototype has no .closest.
    const text = document.createTextNode("hello");
    document.body.appendChild(text);
    // Use Object.defineProperty so target isn't read-only in jsdom.
    const ev = new Event("click", { bubbles: true });
    Object.defineProperty(ev, "target", { value: text });
    // Must NOT throw. Pre-fix this raises:
    //   TypeError: e.target.closest is not a function
    expect(() => document.dispatchEvent(ev)).not.toThrow();
  });

  test("click on Text node INSIDE a data-action element still fires the action (UAT Bug #6)", () => {
    // The real-world case: <div data-action="foo">Enhanced</div> renders
    // a Text node child. A click whose e.target is the text node — which
    // can happen depending on hit-testing precision — must still resolve
    // to the wrapping div's data-action. My earlier 'silently return on
    // non-Element' fix was too strict and broke this case, causing the
    // 'flaky filter' bug: clicks sometimes hit the text, sometimes the
    // div, so the filter ran intermittently.
    const fired = vi.fn();
    /** @type {any} */ (window)._testTextNodeChild = fired;
    const btn = document.createElement("div");
    btn.dataset.action = "_testTextNodeChild";
    btn.appendChild(document.createTextNode("Enhanced"));
    document.body.appendChild(btn);

    // Force the event target to the Text node, not the div.
    const text = btn.firstChild;
    const ev = new Event("click", { bubbles: true });
    Object.defineProperty(ev, "target", { value: text });
    document.dispatchEvent(ev);

    expect(fired).toHaveBeenCalledTimes(1);
    delete (/** @type {any} */ (window)._testTextNodeChild);
  });

  test("keydown with non-Element target is a silent no-op", () => {
    const ev = new KeyboardEvent("keydown", { key: "Escape", bubbles: true });
    Object.defineProperty(ev, "target", {
      value: {
        /* no closest */
      },
    });
    expect(() => document.dispatchEvent(ev)).not.toThrow();
  });

  test("input with non-Element target is a silent no-op", () => {
    const ev = new Event("input", { bubbles: true });
    Object.defineProperty(ev, "target", {
      value: {
        /* no closest */
      },
    });
    expect(() => document.dispatchEvent(ev)).not.toThrow();
  });

  test("change / mousedown / mouseup / contextmenu / dblclick all guard", () => {
    for (const type of ["change", "mousedown", "mouseup", "contextmenu", "dblclick"]) {
      const ev = new Event(type, { bubbles: true });
      Object.defineProperty(ev, "target", {
        value: {
          /* no closest */
        },
      });
      expect(() => document.dispatchEvent(ev)).not.toThrow();
    }
  });
});

// ── data-stop-propagation phase semantics ────────────────────────────────────

describe("data-stop-propagation does not starve descendants (capture-phase regression)", () => {
  test("a native listener INSIDE the marked container still receives clicks", () => {
    // Regression (2026-06-12): the dispatcher runs at document CAPTURE
    // phase; calling e.stopPropagation() there killed the event before it
    // descended to the target — every native listener inside a
    // [data-stop-propagation] container went dead (Leaflet map +/− zoom
    // in the lightbox panel). The stop must happen on the way back UP.
    const container = document.createElement("div");
    container.dataset.stopPropagation = "true";
    const btn = document.createElement("button");
    container.appendChild(btn);
    document.body.appendChild(container);

    const inner = vi.fn();
    btn.addEventListener("click", inner);
    const outer = vi.fn();
    document.body.addEventListener("click", outer);
    try {
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      // Inner native handler MUST fire…
      expect(inner).toHaveBeenCalledOnce();
      // …while the bubble is still stopped at the marked container.
      expect(outer).not.toHaveBeenCalled();
    } finally {
      document.body.removeEventListener("click", outer);
    }
  });
});
