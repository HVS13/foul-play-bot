// ==UserScript==
// @name         Foul Play Companion
// @namespace    https://github.com/HVS13/foul-play-bot
// @version      1.0.0
// @description  Live poke-engine recommendations inside Pokémon Showdown.
// @match        https://play.pokemonshowdown.com/*
// @grant        unsafeWindow
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      127.0.0.1
// @run-at       document-idle
// @noframes
// ==/UserScript==

(() => {
  'use strict';
  if (document.getElementById('foul-play-companion')) return;
  const clientId = crypto.randomUUID();
  let endpoint = GM_getValue('engine', 'http://127.0.0.1:8765');
  let selected = '', latest = null, lastKey = '', busy = false, generation = 0;
  let auto = false, submitted = '', paused = false;
  const host = document.createElement('div');
  host.id = 'foul-play-companion';
  // Shadow DOM keeps Showdown/Showdex styles separate from the companion.
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `
    <style>
      :host{all:initial;position:fixed;right:18px;top:90px;z-index:10000;font:13px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#e9eef5;color-scheme:dark}
      *{box-sizing:border-box}button,input,select{font:inherit}button,select{cursor:pointer}
      button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:2px solid #a2f2cb;outline-offset:3px}
      .panel{width:354px;max-width:calc(100vw - 24px);max-height:calc(100dvh - 110px);overflow:hidden;display:flex;flex-direction:column;background:#111923;border:1px solid #344251;border-radius:18px;box-shadow:0 18px 60px #0006}
      header{padding:18px 20px 14px;border-bottom:1px solid #293440;display:flex;align-items:center;gap:11px;flex-shrink:0}
      .mark{display:grid;place-items:center;width:34px;height:34px;border:1px solid #8bdab1;border-radius:10px;background:#1e3830;color:#a2f2cb;font-size:18px;font-weight:800}
      h1{font-size:16px;letter-spacing:-.4px;margin:0} .subtitle{color:#93a4b6;font-size:11px} .spacer{flex:1}
      button{border:1px solid #354354;background:#1d2937;color:inherit;border-radius:9px;padding:8px 12px;font-weight:600}
      button:hover{background:#2c3d4d}button:disabled{cursor:default;opacity:.4}.icon{padding:4px 9px;font-size:17px;background:transparent}
      main{padding:16px 20px;overflow:auto;min-height:0;scrollbar-width:thin}.controls{padding:12px 20px;border-top:1px solid #293440;flex-shrink:0}.status{display:flex;align-items:center;gap:8px;font-size:12px;color:#a6b7c8;margin-bottom:14px}
      .dot{height:7px;width:7px;background:#90a3b6;border-radius:50%;flex-shrink:0}.status[data-kind=ready] .dot{background:#8bdab1}.status[data-kind=error] .dot{background:#f6a090}.status[data-kind=search] .dot{background:#e9c981}
      label{display:block;font-size:11px;font-weight:700;letter-spacing:.7px;color:#93a4b6;margin-bottom:7px;text-transform:uppercase}
      select,input{width:100%;background:#182330;border:1px solid #344251;color:inherit;padding:9px 10px;border-radius:9px}select{margin-bottom:14px}
      .versus{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:0 0 16px}.pokemon{border:1px solid #293b49;border-radius:11px;padding:10px 11px;min-width:0}
      .name{font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.muted{color:#93a4b6;font-size:11px}.hp{height:4px;background:#293440;border-radius:4px;margin-top:9px;overflow:hidden}.hp span{display:block;height:100%;background:#8bdab1}
      .section{display:flex;justify-content:space-between;align-items:center;margin:4px 0 9px}.eyebrow{font-size:10px;letter-spacing:1px;font-weight:750;color:#93a4b6;text-transform:uppercase}.turn{font-size:11px;color:#a2f2cb}
      .recommendation{background:#1c302a;border:1px solid #456656;border-radius:12px;padding:13px 14px;margin-bottom:10px}.move{font-size:19px;letter-spacing:-.4px;font-weight:700;word-break:break-word}.recommendation .muted{color:#acc3b6;margin-top:3px}
      .policy-row{display:grid;grid-template-columns:1fr 50px;gap:8px;padding:7px 0}.policy-name{font-weight:550}.weight{text-align:right;color:#a2f2cb;font-variant-numeric:tabular-nums}.bar{grid-column:1/-1;height:3px;background:#293440;border-radius:3px;overflow:hidden}.bar span{display:block;height:100%;background:#609e86}
      .empty{padding:22px 14px;text-align:center;border:1px dashed #344251;border-radius:12px;color:#93a4b6;margin:8px 0 12px}
      .metrics{display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid #293440;border-bottom:1px solid #293440;margin:14px 0;padding:11px 0;gap:8px}.metric strong{display:block;font-size:13px;font-weight:650}.metric span{font-size:10px;color:#93a4b6}
      .primary{width:100%;background:#a2f2cb;border-color:#a2f2cb;color:#10231c}.primary:hover{background:#bafadb}
      .actions{display:flex;gap:8px;align-items:center;margin-top:10px}.actions button{flex:1;font-size:11px}.toggle{display:flex;align-items:center;gap:8px;color:#bcc9d7;font-size:12px;text-transform:none;letter-spacing:0;font-weight:500;margin:12px 0 2px}.toggle input{width:auto;accent-color:#8bdab1}
      details{padding:12px 20px;border-top:1px solid #293440;overflow:auto;flex-shrink:0;max-height:260px}summary{cursor:pointer;color:#93a4b6;font-size:11px}details label{margin-top:12px}.save{width:100%;margin-top:8px;font-size:12px}
      footer{padding:11px 20px;border-top:1px solid #293440;display:flex;justify-content:space-between;align-items:center;flex-shrink:0;font-size:10px;color:#93a4b6}a{color:#acc3b6;text-decoration:none}a:hover{text-decoration:underline}
      .launcher{display:none;background:#111923;border:1px solid #456656;box-shadow:0 8px 30px #0004;color:#a2f2cb}
      :host([collapsed]) .panel{display:none}:host([collapsed]) .launcher{display:block}
      :host([dock=left]){left:18px;right:auto}
      @media(max-width:600px){:host{top:auto;bottom:12px;right:12px}.panel{max-height:calc(100dvh - 80px)}:host([dock=left]){left:12px}}
      @media(prefers-reduced-motion:no-preference){.hp span,.bar span{transition:width .2s}}
    </style>
    <button class="launcher" aria-label="Open Foul Play companion">◈ Foul Play</button>
    <section class="panel" aria-label="Foul Play companion">
      <header><div class="mark" aria-hidden="true">◈</div><div><h1>Foul Play</h1><div class="subtitle">BATTLE COMPANION</div></div><div class="spacer"></div><button id="collapse" class="icon" aria-label="Minimize companion">−</button></header>
      <main>
        <div class="status" id="status" role="status" aria-live="polite"><span class="dot"></span><span id="message">Waiting for Showdown…</span></div>
        <label for="battle">Your battle</label><select id="battle"><option value="">Open a singles battle to begin</option></select>
        <div class="versus" id="versus"></div>
        <div class="section"><span class="eyebrow">Recommended action</span><span class="turn" id="turn">—</span></div>
        <div id="recommendation" class="empty">Start the local engine, then join a battle as a player.</div>
        <div id="policy"></div>
        <div class="metrics"><div class="metric"><strong id="risk">—</strong><span>Risk mode</span></div><div class="metric"><strong id="confidence">—</strong><span title="Top policy weight divided by second weight; not win probability">Policy lead</span></div><div class="metric"><strong id="latency">—</strong><span>Search time</span></div></div>
      </main>
      <div class="controls"><button id="apply" class="primary" disabled>Apply recommendation</button>
        <label class="toggle"><input id="auto" type="checkbox">Play automatically in this battle</label>
        <div class="actions"><button id="pause">Pause analysis</button><button id="refresh">Analyze again</button></div>
      </div>
      <details><summary>Connection & display</summary><label for="engine">Local engine address</label><input id="engine" type="url" spellcheck="false"><button id="save" class="save">Save connection</button><label for="dock">Panel position</label><select id="dock"><option value="right">Right</option><option value="left">Left</option></select><p class="muted">Python + poke-engine must be running with --bot-mode browser. Only 127.0.0.1 is supported.</p></details>
      <footer><span>Policy weights ≠ win probability</span><a id="dashboard" target="_blank" rel="noopener">Dashboard ↗</a></footer>
    </section>`;
  document.body.appendChild(host);
  const $ = id => root.getElementById(id);
  const setText = (id, value) => { $(id).textContent = value; };
  const status = (message, kind = '') => { setText('message', message); $('status').dataset.kind = kind; };
  $('engine').value = endpoint;
  $('dashboard').href = endpoint;
  host.setAttribute('dock', GM_getValue('dock', 'right'));
  $('dock').value = host.getAttribute('dock');
  $('dock').onchange = () => { host.setAttribute('dock', $('dock').value); GM_setValue('dock', $('dock').value); };
  $('collapse').onclick = () => host.setAttribute('collapsed', '');
  root.querySelector('.launcher').onclick = () => host.removeAttribute('collapsed');
  function invalidate() { generation++; latest = null; lastKey = ''; auto = false; $('auto').checked = false; $('apply').disabled = true; }
  $('save').onclick = () => {
    try {
      const url = new URL($('engine').value);
      if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error();
      endpoint = url.origin; GM_setValue('engine', endpoint); $('dashboard').href = endpoint;
      invalidate(); status('Connection saved. Waiting for a battle.');
    } catch (_) { status('Use http://127.0.0.1:PORT', 'error'); }
  };
  $('auto').onchange = () => { auto = $('auto').checked; if (auto) apply(); };
  $('pause').onclick = () => { paused = !paused; invalidate(); setText('pause', paused ? 'Resume analysis' : 'Pause analysis'); if (paused) status('Analysis paused'); };
  $('refresh').onclick = () => { lastKey = ''; latest = null; $('apply').disabled = true; tick(); };
  $('battle').onchange = () => { selected = $('battle').value; invalidate(); };
  function rooms() { return Object.values(unsafeWindow.app?.rooms || {}).filter(r => r.id?.startsWith('battle-') && r.request?.side); }
  function currentRoom() { return unsafeWindow.app?.rooms?.[selected]; }
  function requestKey(room) { return `${room.id}:${JSON.stringify(room.request)}:${room.battle?.stepQueue?.length}`; }
  function available(room) { return room?.battle?.gameType === 'singles' && room.request && !room.request.wait && !room.choice?.waiting && !room.battleEnded && !room.battle.ended; }
  function clearCards(message) {
    $('recommendation').className = 'empty'; setText('recommendation', message);
    $('policy').replaceChildren(); $('versus').replaceChildren();
    for (const id of ['risk', 'confidence', 'latency', 'turn']) setText(id, '—');
  }
  function pretty(move) {
    if (move?.startsWith('switch ')) return 'Switch → ' + move.slice(7);
    const dex = unsafeWindow.Dex;
    const base = move?.replace(/-(tera|mega)$/, '');
    const name = dex?.moves?.get?.(base)?.name || base || '—';
    return name + (move?.endsWith('-tera') ? ' · Tera' : move?.endsWith('-mega') ? ' · Mega' : '');
  }
  function render(response) {
    const state = response.state || {}, battle = state.battle, result = state.decision?.result;
    $('recommendation').className = 'recommendation';
    $('recommendation').replaceChildren();
    const move = document.createElement('div'); move.className = 'move';
    move.textContent = result ? pretty(result.choice) : 'Team preview';
    const description = document.createElement('div'); description.className = 'muted';
    description.textContent = result ? 'Selected by poke-engine · current request' : response.command?.split('|')[0] || 'Choose your lead';
    $('recommendation').append(move, description);
    $('policy').replaceChildren();
    (result?.policy || []).slice(0, 4).forEach(item => {
      const row = document.createElement('div'); row.className = 'policy-row';
      const label = document.createElement('span'); label.className = 'policy-name'; label.textContent = pretty(item.move);
      const weight = Math.max(0, Math.min(1, Number(item.weight) || 0));
      const value = document.createElement('span'); value.className = 'weight'; value.textContent = `${(weight * 100).toFixed(1)}%`;
      const bar = document.createElement('div'); bar.className = 'bar'; const fill = document.createElement('span'); fill.style.width = `${weight * 100}%`; bar.appendChild(fill);
      row.append(label, value, bar); $('policy').appendChild(row);
    });
    $('versus').replaceChildren();
    for (const [side, title] of [[battle?.user, 'YOUR ACTIVE'], [battle?.opponent, 'OPPONENT']]) {
      const p = side?.active, card = document.createElement('div'); card.className = 'pokemon';
      const label = document.createElement('div'); label.className = 'eyebrow'; label.textContent = title;
      const name = document.createElement('div'); name.className = 'name'; name.textContent = p?.nickname || p?.name || 'Preview';
      const hp = Math.max(0, Math.min(1, p?.hp_pct || 0));
      const detail = document.createElement('div'); detail.className = 'muted'; detail.textContent = p ? `${Math.round(hp * 100)}% HP${p.status ? ' · ' + p.status : ''}` : 'Select your team';
      const bar = document.createElement('div'); bar.className = 'hp'; const fill = document.createElement('span'); fill.style.width = `${hp * 100}%`; bar.appendChild(fill);
      card.append(label, name, detail, bar); $('versus').appendChild(card);
    }
    setText('turn', `Turn ${battle?.turn || 0}`); setText('risk', result?.risk_mode || '—');
    setText('confidence', result ? (result.confidence_ratio == null ? 'Single option' : `${result.confidence_ratio.toFixed(2)}×`) : '—');
    setText('latency', result ? `${result.total_search_time_ms} ms` : '—');
    status('Recommendation ready', 'ready');
    $('apply').disabled = !response.command;
  }
  function apply() {
    const room = currentRoom();
    if (paused || !latest || !available(room) || latest.key !== requestKey(room) || latest.response.battle_tag !== room.id || latest.response.rqid !== room.request.rqid || submitted === latest.key) return;
    const command = latest.response.command;
    if (!/^\/(choose move |switch \d+\||team \d+\|)/.test(command || '')) return;
    // Use Showdown's own choice path so waiting/undo state stays consistent.
    if (typeof room.sendDecision !== 'function' || typeof room.endTurn !== 'function') {
      status('Unsupported Showdown client; choose the move manually', 'error'); return;
    }
    const choice = command.split('|')[0].replace(/^\/(?:choose )?/, '');
    room.choice = room.choice || { choices: [] };
    if (choice.startsWith('team ')) room.choice.teamPreview = choice.slice(5).split('').map(Number);
    else room.choice.choices = [choice];
    try {
      room.endTurn();
      submitted = latest.key; $('apply').disabled = true;
      status('Choice submitted · waiting for opponent');
    } catch (_) {
      auto = false; $('auto').checked = false;
      status('Unable to apply; choose the move manually', 'error');
    }
  }
  $('apply').onclick = apply;
  function tick() {
    const candidates = rooms();
    const ids = candidates.map(r => r.id);
    if ($('battle').dataset.ids !== ids.join(',')) {
      $('battle').dataset.ids = ids.join(','); $('battle').replaceChildren();
      for (const room of candidates) { const option = document.createElement('option'); option.value = room.id; option.textContent = room.title || room.id; $('battle').appendChild(option); }
      if (!candidates.length) { const option = document.createElement('option'); option.value = ''; option.textContent = 'Open a singles battle to begin'; $('battle').appendChild(option); }
    }
    if (!ids.includes(selected)) {
      selected = ids.includes(unsafeWindow.app?.curRoom?.id) ? unsafeWindow.app.curRoom.id : ids[0] || '';
      invalidate(); clearCards('Join a battle as a player to see recommendations.');
    }
    $('battle').value = selected;
    if (paused) return;
    const room = currentRoom();
    if (!room) { status(unsafeWindow.app ? 'No player battle open' : 'Waiting for the Showdown client…'); return; }
    const history = Array.from(room.battle?.stepQueue || []);
    if (room.battle?.gameType !== 'singles' || !history.length) { latest = null; $('apply').disabled = true; clearCards('Only singles battles are supported.'); status('Unsupported battle', 'error'); return; }
    if (!available(room)) { latest = null; lastKey = ''; $('apply').disabled = true; clearCards(room.battleEnded ? 'Battle finished. Open another battle to continue.' : 'Waiting for the next request…'); status(room.battleEnded ? 'Battle finished' : 'Waiting for opponent'); return; }
    const key = requestKey(room);
    if (latest && latest.key !== key) { latest = null; $('apply').disabled = true; clearCards('Analyzing the current position…'); }
    if (key === lastKey || busy) return;
    busy = true; lastKey = key;
    const version = generation;
    status('Analyzing position…', 'search');
    GM_xmlhttpRequest({
      method: 'POST', url: endpoint + '/api/browser', headers: { 'Content-Type': 'application/json' }, timeout: 120000,
      data: JSON.stringify({ client_id: clientId, battle_tag: room.id, pokemon_format: room.id.split('-')[1], history, request: room.request, time_remaining: room.battle.kickingInactive }),
      onload: response => {
        busy = false;
        if (version !== generation || !available(currentRoom()) || requestKey(currentRoom()) !== key) return;
        try {
          const data = JSON.parse(response.responseText);
          if (response.status !== 200) { lastKey = ''; status(data.error || `Engine returned ${response.status}`, 'error'); return; }
          if (data.battle_tag !== room.id || data.rqid !== room.request.rqid) return;
          latest = { key, response: data }; render(data); if (auto) apply();
        } catch (_) { lastKey = ''; status('Invalid engine response', 'error'); }
      },
      onerror: () => { busy = false; lastKey = ''; if (version === generation) status('Engine offline · start Python in browser mode', 'error'); },
      ontimeout: () => { busy = false; lastKey = ''; if (version === generation) status('Search timed out · check the engine log', 'error'); }
    });
  }
  tick(); setInterval(tick, 1000);
})();
