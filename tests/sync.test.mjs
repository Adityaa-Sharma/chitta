// Extracts sync() from the dashboard and exercises it against a stub DOM.
// sync() is the only non-obvious logic in the UI: it replaced an innerHTML
// rewrite that ran every 2.5s, so a bug here means leaked or vanishing rows.
import { readFileSync } from "node:fs";
import assert from "node:assert";

const html = readFileSync(new URL("../chitta/ui/index.html", import.meta.url), "utf8");
const src = html.match(/function sync\(container[\s\S]*?\n}\n/)[0];

globalThis.requestAnimationFrame = (f) => f();
globalThis.setTimeout = (f) => f();

const mkEl = () => ({
  children: [], style: {}, className: "", _listeners: {},
  classList: {
    _s: new Set(),
    add(...c){ c.forEach(x=>this._s.add(x)); },
    remove(...c){ c.forEach(x=>this._s.delete(x)); },
    contains(c){ return this._s.has(c); },
  },
  appendChild(c){ this.children = this.children.filter(x=>x!==c); this.children.push(c); return c; },
  remove(){ if (this.parent) this.parent.children = this.parent.children.filter(x=>x!==this); },
  addEventListener(ev, fn){ this._listeners[ev] = fn; },
});
const container = mkEl();
container.appendChild = function(c){
  this.children = this.children.filter(x=>x!==c); c.parent = this; this.children.push(c); return c;
};

const sync = new Function("mkEl", src + "; return sync;")(mkEl);
const build = () => mkEl();
const update = (el, it) => { el.text = it.v; };
const key = it => it.id;

// 1. initial insert
sync(container, [{id:"a",v:1},{id:"b",v:2}], key, build, update);
assert.equal(container.children.length, 2, "two rows inserted");
const elA = container._rows.get("a");

// 2. update must NOT recreate nodes — that was the original flicker bug
sync(container, [{id:"a",v:9},{id:"b",v:2}], key, build, update);
assert.equal(container._rows.get("a"), elA, "node identity preserved across refresh");
assert.equal(elA.text, 9, "existing node updated in place");
assert.equal(container.children.length, 2, "no duplicate rows");

// 3. removal
sync(container, [{id:"b",v:2}], key, build, update);
assert.equal(container._rows.size, 1, "removed key dropped from map");
assert.equal(container._rows.has("a"), false);

// 4. re-add after removal
sync(container, [{id:"a",v:5},{id:"b",v:2}], key, build, update);
assert.equal(container._rows.size, 2, "re-added");
assert.notEqual(container._rows.get("a"), elA, "re-added row is a fresh node");

// 5. churn must not leak
for (let i = 0; i < 50; i++) sync(container, [{id:"a",v:i},{id:"b",v:i}], key, build, update);
assert.equal(container._rows.size, 2, "no leak after 50 refreshes");
assert.equal(container.children.length, 2, "no orphan children after churn");

console.log("sync() ok — identity preserved, no duplicates, no leak over 50 refreshes");
