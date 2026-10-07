const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() { this.children = []; this.dataset = {}; this.style = {}; this.attributes = {}; this.value = ''; this.checked = false; }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this.attributes[name]; }
  attachShadow() { return this.root = new Root(); }
}
class Root extends Element {
  constructor() { super(); this.elements = {}; }
  getElementById(id) { return this.elements[id] ||= new Element(); }
  querySelector() { return this.getElementById('launcher'); }
}
function setup() {
  const body = new Element(), requests = [], sent = [];
  const room = {
    id: 'battle-gen9ou-123', title: 'TestUser vs. Opponent',
    request: { rqid: 1, side: { name: 'TestUser', id: 'p1' }, active: [{}] },
    battle: { stepQueue: ['|gametype|singles'], gameType: 'singles' },
    choice: { choices: [] }, sendDecision() {},
    endTurn() {
      const choice = this.request.teamPreview ? 'team ' + this.choice.teamPreview.join('') : this.choice.choices[0];
      sent.push(choice + '|' + this.request.rqid); this.choice.waiting = true;
    },
  };
  const context = {
    document: { body, getElementById: () => null, createElement: () => new Element() },
    unsafeWindow: { app: { rooms: { [room.id]: room }, curRoom: room } },
    crypto: { randomUUID: () => 'test-tab' },
    GM_getValue: (_, fallback) => fallback, GM_setValue() {},
    GM_xmlhttpRequest: request => requests.push(request), URL,
    setInterval: callback => { context.tick = callback; },
  };
  vm.runInNewContext(fs.readFileSync('fp/gui/foul-play.user.js', 'utf8'), context);
  const root = body.children[0].root;
  function respond(request = requests.at(-1), command = '/choose move tackle|1') {
    request.onload({ status: 200, responseText: JSON.stringify({
      battle_tag: room.id, rqid: JSON.parse(request.data).request.rqid, command,
      state: { battle: { turn: 1 }, decision: { result: {
        choice: 'tackle', policy: [{ move: 'tackle', weight: 1 }],
        risk_mode: 'safe', confidence_ratio: null, total_search_time_ms: 120,
      } } },
    }) });
  }
  return { root, room, requests, sent, tick: () => context.tick(), respond };
}
test('manual default only submits when Apply is clicked, once per request', () => {
  const h = setup(); h.respond();
  assert.equal(h.sent.length, 0);
  assert.equal(h.root.getElementById('apply').disabled, false);
  h.root.getElementById('apply').onclick(); h.root.getElementById('apply').onclick();
  assert.deepEqual(h.sent, ['move tackle|1']);
});
test('stale response cannot enable Apply after request advances', () => {
  const h = setup(), old = h.requests[0];
  h.room.request.rqid = 2; h.tick(); h.respond(old);
  assert.equal(h.root.getElementById('apply').disabled, true);
  h.root.getElementById('apply').onclick(); assert.equal(h.sent.length, 0);
  h.tick(); h.respond(h.requests.at(-1), '/choose move tackle|2');
  h.root.getElementById('apply').onclick(); assert.deepEqual(h.sent, ['move tackle|2']);
});
test('Apply checks native manual choice, pause and finished battles immediately', () => {
  for (const change of [h => { h.room.choice.waiting = true; }, h => { h.room.battleEnded = true; }, h => h.root.getElementById('pause').onclick()]) {
    const h = setup(); h.respond(); change(h); h.root.getElementById('apply').onclick();
    assert.equal(h.sent.length, 0);
  }
});
test('automatic mode is opt-in and resets when battle changes', () => {
  const h = setup(); h.respond();
  const auto = h.root.getElementById('auto'); auto.checked = true; auto.onchange();
  assert.deepEqual(h.sent, ['move tackle|1']);
  h.room.id = 'battle-gen9ou-456'; h.tick(); assert.equal(auto.checked, false);
});
test('forced switch and team preview use a single native submission', () => {
  const h = setup(); h.room.request.forceSwitch = [true];
  h.tick(); h.respond(h.requests[0], '/switch 2|1'); h.tick();
  h.respond(h.requests.at(-1), '/switch 2|1'); h.root.getElementById('apply').onclick();
  assert.deepEqual(h.sent, ['switch 2|1']);
  const t = setup(); t.room.request.teamPreview = true;
  t.respond(t.requests[0], '/team 213456|1'); t.tick(); t.respond(t.requests.at(-1), '/team 213456|1');
  t.root.getElementById('apply').onclick(); assert.deepEqual(t.sent, ['team 213456|1']);
});
test('doubles, offline engine and malformed responses never submit', () => {
  const h = setup(); h.room.battle.gameType = 'doubles'; h.tick();
  h.respond(); h.root.getElementById('apply').onclick(); assert.equal(h.sent.length, 0);
  const offline = setup(); offline.requests[0].onerror(); assert.equal(offline.root.getElementById('apply').disabled, true);
  const bad = setup(); bad.requests[0].onload({ status: 200, responseText: 'not JSON' });
  assert.equal(bad.root.getElementById('apply').disabled, true);
});
