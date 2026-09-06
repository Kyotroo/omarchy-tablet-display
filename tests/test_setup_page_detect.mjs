// Regression test for the setup page's platform-detection/store logic --
// run under Node's vm module against a minimal DOM mock, not a browser.
// Exists specifically because of a real bug found live: on a Galaxy Tab S10
// FE, Chrome's legacy navigator.userAgent read as full desktop Linux
// ("X11; Linux x86_64", no "Android" anywhere), which silently broke the
// store-link step. This exercises the actual <script> body from
// index.html, not a reimplementation of it, so a regression here would
// have caught that bug before it shipped.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const pagePath = path.join(here, "..", "daemon", "setup_page", "index.html");
const html = fs.readFileSync(pagePath, "utf8");

const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(scriptMatch, "expected exactly one inline <script> block in index.html");
const scriptBody = scriptMatch[1];

function makeElement(id) {
  return {
    id,
    textContent: "",
    hidden: true,
    className: "",
    href: "",
    target: "",
    rel: "",
    children: [],
    appendChild(child) { this.children.push(child); },
    addEventListener(event, handler) {
      this._listeners = this._listeners || {};
      this._listeners[event] = handler;
    },
    removeEventListener() {},
  };
}

function runPage({ userAgent, userAgentData, platform, maxTouchPoints, vncPort, vncPassword }) {
  const elements = {};
  for (const id of [
    "reportStatus", "connectButton", "connectLabel", "storeStep",
    "storeStepLabel", "storeLinks", "manualAddress",
    "passwordBlock", "manualUsername", "manualPassword",
  ]) {
    elements[id] = makeElement(id);
  }

  const fetchCalls = [];
  const sandbox = {
    navigator: { userAgent, userAgentData, platform, maxTouchPoints: maxTouchPoints || 0 },
    location: { hostname: "192.168.1.50", search: "" },
    document: {
      getElementById: (id) => elements[id],
      createElement: (tag) => makeElement(null),
      hidden: false,
      addEventListener() {},
      removeEventListener() {},
    },
    window: { screen: { width: 1600, height: 900 }, devicePixelRatio: 2, location: { href: "" } },
    fetch: (...args) => {
      fetchCalls.push(args);
      return Promise.resolve({ ok: true });
    },
    setTimeout, clearTimeout, console,
    URLSearchParams,
  };
  sandbox.window.screen = sandbox.window.screen; // keep window.screen == top-level screen usage below
  sandbox.screen = sandbox.window.screen;
  sandbox.devicePixelRatio = sandbox.window.devicePixelRatio;

  const patched = scriptBody
    .replace(/__VNC_PORT__/, String(vncPort ?? 5900))
    .replace(/__VNC_USERNAME__/, "tablet")
    .replace(/__VNC_PASSWORD__/, vncPassword ?? "");
  vm.createContext(sandbox);
  vm.runInContext(patched, sandbox);
  return elements;
}

// -- the exact bug: desktop-style UA, but Client Hints report Android -----
{
  const els = runPage({
    userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    userAgentData: { platform: "Android" },
    platform: "Linux x86_64",
  });
  assert.equal(els.storeStep.hidden, false, "store step must be shown");
  assert.equal(els.storeLinks.children.length, 1, "exactly one store link for a confidently-detected platform");
  assert.match(els.storeLinks.children[0].textContent, /Google Play/);
}

// -- legacy UA still works when Client Hints are unavailable (Firefox) ----
{
  const els = runPage({
    userAgent: "Mozilla/5.0 (Linux; Android 14; SM-X620) AppleWebKit/537.36 Chrome/128.0 Mobile Safari/537.36",
    userAgentData: undefined,
    platform: "Linux armv8l",
  });
  assert.equal(els.storeLinks.children.length, 1);
  assert.match(els.storeLinks.children[0].textContent, /Google Play/);
}

// -- iPadOS masquerading as desktop Safari (Mac + multi-touch) -------------
{
  const els = runPage({
    userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    userAgentData: undefined,
    platform: "MacIntel",
    maxTouchPoints: 5,
  });
  assert.equal(els.storeLinks.children.length, 1);
  assert.match(els.storeLinks.children[0].textContent, /App Store/);
}

// -- detection genuinely inconclusive: show every option, not none --------
{
  const els = runPage({
    userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36",
    userAgentData: undefined,
    platform: "Linux x86_64",
  });
  assert.equal(els.storeStep.hidden, false, "must still show the step rather than hide it silently");
  assert.equal(els.storeLinks.children.length, 3, "all three stores when detection can't tell");
}

// -- password shown only when the session is actually encrypted -----------
{
  const els = runPage({
    userAgent: "Mozilla/5.0 (Linux; Android 14; SM-X620) AppleWebKit/537.36 Chrome/128.0 Mobile Safari/537.36",
    userAgentData: undefined,
    platform: "Linux armv8l",
    vncPassword: "Ab3dEfGh9k",
  });
  assert.equal(els.passwordBlock.hidden, false);
  assert.equal(els.manualPassword.textContent, "Ab3dEfGh9k");
  assert.equal(els.manualUsername.textContent, "tablet");
}
{
  const els = runPage({
    userAgent: "Mozilla/5.0 (Linux; Android 14; SM-X620) AppleWebKit/537.36 Chrome/128.0 Mobile Safari/537.36",
    userAgentData: undefined,
    platform: "Linux armv8l",
  });
  assert.equal(els.passwordBlock.hidden, true, "must stay hidden for an unencrypted session");
}

console.log("PASS: setup page platform detection (6 scenarios)");
