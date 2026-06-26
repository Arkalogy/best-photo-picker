// @ts-check
/**
 * P8 — action-registry.mjs tests.
 *
 * The registry is the strict half of the dispatcher migration: handlers
 * register by name + signature, the click dispatcher consults the registry
 * before window. These tests pin the contract.
 */

import { describe, expect, test, beforeEach } from "vitest";

import {
  registerAction,
  replaceAction,
  lookupAction,
  _registeredNames,
  _resetRegistryForTests,
} from "../bpp/web/static/js/modules/action-registry.mjs";

beforeEach(() => {
  _resetRegistryForTests();
});

describe("registerAction", () => {
  test("stores a handler retrievable via lookupAction", () => {
    function setOverride() {}
    registerAction("setOverride", setOverride);
    expect(lookupAction("setOverride")).toBe(setOverride);
  });

  test("registered name appears in _registeredNames", () => {
    registerAction("a", () => {});
    registerAction("b", () => {});
    expect(_registeredNames().sort()).toEqual(["a", "b"]);
  });

  test("duplicate registration throws with the previous registrant's name", () => {
    function first() {}
    function second() {}
    registerAction("foo", first);
    expect(() => registerAction("foo", second)).toThrow(/already registered/);
    expect(() => registerAction("foo", second)).toThrow(/first/);
  });

  test("anonymous prior handler shows <anonymous> in the error", () => {
    registerAction("foo", () => {});
    expect(() => registerAction("foo", () => {})).toThrow(/<anonymous>/);
  });
});

describe("replaceAction", () => {
  test("overrides an existing registration without throwing", () => {
    function original() {}
    function override() {}
    registerAction("foo", original);
    replaceAction("foo", override);
    expect(lookupAction("foo")).toBe(override);
  });

  test("creates a new registration when none exists", () => {
    function override() {}
    replaceAction("foo", override);
    expect(lookupAction("foo")).toBe(override);
  });
});

describe("lookupAction", () => {
  test("returns undefined for unregistered names", () => {
    expect(lookupAction("never_registered")).toBeUndefined();
  });
});

describe("window.__bppActionRegistry bridge", () => {
  test("exposes the underlying Map on window for the dispatcher", () => {
    registerAction("foo", () => 42);
    // The dispatcher does window.__bppActionRegistry.get(name) — exercise
    // that exact path.
    const fn = globalThis.window.__bppActionRegistry.get("foo");
    expect(typeof fn).toBe("function");
    expect(fn()).toBe(42);
  });

  test("registry.get returns undefined for unregistered names", () => {
    expect(globalThis.window.__bppActionRegistry.get("nope")).toBeUndefined();
  });
});

describe("dispatcher integration semantics", () => {
  // We don't load the real dispatcher here (it's a script-tag global file
  // that doesn't ES-export cleanly); we instead simulate the dispatcher's
  // lookup priority: registry first, window second.

  test("dispatcher should prefer registry handler over window global of same name", () => {
    function fromRegistry() {
      return "registry";
    }
    function fromWindow() {
      return "window";
    }
    registerAction("doThing", fromRegistry);
    globalThis.window.doThing = fromWindow;

    // Simulate the lookup the dispatcher does:
    const registered = globalThis.window.__bppActionRegistry.get("doThing");
    const fn = registered || globalThis.window.doThing;
    expect(fn()).toBe("registry");

    delete globalThis.window.doThing;
  });

  test("dispatcher falls back to window when no registry hit", () => {
    function fromWindow() {
      return "window";
    }
    globalThis.window.legacyHandler = fromWindow;
    const registered = globalThis.window.__bppActionRegistry.get("legacyHandler");
    const fn = registered || globalThis.window.legacyHandler;
    expect(fn()).toBe("window");
    delete globalThis.window.legacyHandler;
  });
});
