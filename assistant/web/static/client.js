/* Marbly: the client-facing experience. Same backend, no calculations here. Dev pages sit behind the gear. */
(() => {
  const L = window.LY; const { api, stream, el, linkify, openEvidence, toast, usd } = L;
  const main = document.getElementById('main');
  const kids = (...xs) => xs.flat().filter(x => x !== null && x !== undefined);
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  let intro = null, feed = null, chips = null, bell = null, book = null, tourBox = null, headAvatar = null, pending = 0;

  // ---------- avatar: a marble of white and blue with gold streaks, big eyes only ----------
  function avatar(size = 44) {
    const uid = 'm' + Math.random().toString(36).slice(2, 7);
    const svg = `<svg viewBox="0 0 100 100" width="${size}" height="${size}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <radialGradient id="${uid}g" cx="38%" cy="32%" r="75%"><stop offset="0%" stop-color="#ffffff"/><stop offset="55%" stop-color="#eef2f8"/><stop offset="100%" stop-color="#c9d6e8"/></radialGradient>
        <clipPath id="${uid}c"><circle cx="50" cy="50" r="46"/></clipPath>
      </defs>
      <circle cx="50" cy="50" r="46" fill="url(#${uid}g)"/>
      <g clip-path="url(#${uid}c)" fill="none" stroke-linecap="round">
        <path d="M-5 62 C 20 40, 40 80, 70 55 S 105 40, 115 58" stroke="#4f7fc0" stroke-opacity=".45" stroke-width="9"/>
        <path d="M-5 78 C 25 62, 45 96, 80 72 S 100 70, 112 80" stroke="#6a97d6" stroke-opacity=".35" stroke-width="6"/>
        <path d="M0 30 C 22 14, 48 42, 72 24 S 96 18, 108 30" stroke="#5b86c4" stroke-opacity=".3" stroke-width="7"/>
        <path d="M-5 50 C 25 34, 42 60, 68 46 S 98 36, 110 50" stroke="#d9b24a" stroke-opacity=".9" stroke-width="3.4"/>
        <path d="M10 88 C 30 76, 60 92, 90 74" stroke="#c9a227" stroke-opacity=".8" stroke-width="2.6"/>
        <path d="M5 18 C 28 10, 50 26, 80 12" stroke="#e2c266" stroke-opacity=".8" stroke-width="2.2"/>
      </g>
      <ellipse cx="34" cy="26" rx="12" ry="7" fill="#ffffff" fill-opacity=".85" transform="rotate(-25 34 26)"/>
      <g class="eyes">
        <circle cx="38" cy="54" r="9.5" fill="#1d2430"/><circle cx="62" cy="54" r="9.5" fill="#1d2430"/>
        <circle cx="41" cy="50.5" r="3.4" fill="#fff"/><circle cx="65" cy="50.5" r="3.4" fill="#fff"/>
        <circle cx="35.5" cy="57.5" r="1.5" fill="#fff" fill-opacity=".9"/><circle cx="59.5" cy="57.5" r="1.5" fill="#fff" fill-opacity=".9"/>
      </g>
      <circle cx="50" cy="50" r="46" fill="none" stroke="#b9c7dc" stroke-width="1.2"/>
    </svg>`;
    const wrap = el('span', { class: 'marbly-avatar', html: svg });
    return wrap;
  }
  const think = (on) => { pending += on ? 1 : -1; if (headAvatar) headAvatar.classList.toggle('thinking', pending > 0); };
  // Dim everything except one element and pulse it; any click clears it.
  function dimSpot(target, text) {
    document.querySelector('.dim')?.remove();
    if (!target) return;
    const ov = el('div', { class: 'dim' }, el('div', { class: 'dim-text' }, text || ''));
    target.classList.add('spot', 'spot-lit');
    const clear = () => { ov.remove(); target.classList.remove('spot-lit'); target.removeEventListener('click', clear); };
    ov.onclick = clear; target.addEventListener('click', clear);
    document.body.append(ov);
  }
  const thinkingBubble = (text) => say('', 'Marbly', el('div', { class: 'row' }, el('span', { class: 'marbly-avatar thinking' , html: avatar(30).innerHTML }), el('span', { class: 'muted' }, text)));

  // ---------- feed ----------
  const say = (text, who = 'Marbly', node = null, memo = false) => {
    const b = el('div', { class: `bubble ${who === 'Marbly' ? 'm' : 'u'}` }, who === 'Marbly' ? el('div', { class: 'who' }, avatar(22), ' Marbly') : el('div', { class: 'who' }, who), node || text);
    if (memo) b.append(el('div', { class: 'memo-row' }, el('button', { class: 'ghost small', onclick: (e) => { e.target.disabled = true; b.append(memoryForm(typeof text === 'string' ? text.slice(0, 140) : '')); } }, '+ add to memory')));
    feed.append(b); b.scrollIntoView({ block: 'start', behavior: 'smooth' }); return b;
  };
  const closePanels = () => { for (const p of document.querySelectorAll('.sidepanel')) p.remove(); };
  const panel = (title, ...children) => { closePanels(); const p = el('div', { class: 'sidepanel' }, el('div', { class: 'drawer-head' }, el('strong', {}, title), el('button', { class: 'ghost', onclick: closePanels }, 'close')), ...kids(children)); document.body.append(p); return p; };

  // ---------- the pinned setup (tour) ----------
  const TOUR = { facts: 'Straighten out the facts', alerts: "Look at today's critical alerts", memory: 'See what I remember about your kitchen', order: 'Place your first order and see it through' };
  let tour = null;
  function spotlight() {
    for (const b of [bell, book]) b && b.classList.remove('spot');
    for (const c of chips.querySelectorAll('button')) c.classList.remove('spot');
    if (!tour || tour.complete) return;
    if (tour.current === 'alerts' && bell) bell.classList.add('spot');
    if (tour.current === 'memory' && book) book.classList.add('spot');
    if (tour.current === 'order') { const c = [...chips.querySelectorAll('button')].find(b => /next Alder order/i.test(b.textContent)); c && c.classList.add('spot'); }
  }
  function renderTour() {
    if (!tourBox) return;
    if (!tour || tour.complete) { tourBox.classList.add('hidden'); spotlight(); return; }
    tourBox.classList.remove('hidden');
    const action = { facts: ['Answer the questions', () => { const q = [...document.querySelectorAll('.qcard')].pop(); q ? q.scrollIntoView({ block: 'start', behavior: 'smooth' }) : runOnboarding(); }],
      alerts: ['Open the alerts', openInbox], memory: ['Open memory', openMemory], order: ['Start the order', () => openWizard('V1')] };
    tourBox.replaceChildren(el('div', { class: 'tour-head' }, avatar(28), el('strong', {}, 'Getting set up'), el('span', { class: 'small muted' }, `${Object.values(tour.done).filter(Boolean).length} of 4 done`)),
      ...tour.steps.map((s, i) => { const done = tour.done[s], cur = tour.current === s; return el('div', { class: `tstep ${done ? 'done' : cur ? 'on' : ''}` }, el('span', { class: 'n' }, done ? '✓' : String(i + 1)), el('span', { class: 'label' }, TOUR[s]), cur ? el('button', { onclick: action[s][1] }, action[s][0]) : null); }));
    spotlight();
  }
  async function refreshTour() { try { tour = await api('/api/client/tour'); } catch { tour = null; } renderTour(); return tour; }
  async function markTour(step) { const before = tour && tour.current; tour = await api('/api/client/tour', { method: 'POST', body: { step } }); renderTour(); if (before === step) nextStepMessage(); }
  function nextStepMessage() {
    if (!tour) return;
    if (tour.complete) { say("That's the setup done. The bell, the book and the order steps are always here, and the chips below are good places to start. Ask me anything about the numbers."); return; }
    if (tour.current === 'memory') { say("Step 3. Everything you just told me is in Memory: your rules, your preferences, and the facts I am tracking with where each one came from. Open the book to see it."); introduceCapabilities(); }
    if (tour.current === 'order') { say("Step 4, the big one. Let's place your first order together: I size it from your counts, recipes and rules, show every line and why, and nothing is sent until your operator approves it. Click the pulsing chip or the button above."); const chip = [...chips.querySelectorAll('button')].find(b => /next Alder order/i.test(b.textContent)); dimSpot(chip, 'Your first order starts here.'); }
  }

  // ---------- alerts inbox ----------
  async function refreshBell() { try { const a = await api('/api/alerts'); bell.querySelector('.count')?.remove(); if (a.unseen_critical) bell.append(el('span', { class: 'count' }, String(a.unseen_critical))); bell.title = `${a.counts.HIGH} critical · ${a.counts.MED} watch · ${a.counts.LOW} info`; return a; } catch { return null; } }
  function alertCard(x, compact = false) {
    const c = el('div', { class: `alertcard ${x.severity}${x.seen ? ' seen' : ''}` },
      el('div', { class: 'row' }, el('span', { class: `badge ${x.severity}` }, x.severity === 'HIGH' ? 'critical' : x.severity === 'MED' ? 'watch' : 'info'), el('strong', {}, x.title)),
      compact ? null : el('p', { class: 'small' }, x.summary),
      el('p', { class: 'small' }, el('strong', {}, 'Next step: '), x.next_step),
      el('div', { class: 'chips' },
        el('button', { onclick: () => { closePanels(); sendText(x.action && x.action.kind === 'ask' ? x.action.prompt : `Tell me more about this alert: ${x.title}`); } }, 'Ask Marbly'),
        x.action && x.action.kind === 'order' ? el('button', { onclick: () => { closePanels(); openWizard(x.action.vendor); } }, x.action.label) : null,
        el('button', { onclick: () => { if (x.citations[0]) openEvidence(x.citations[0].resource, x.citations[0].ref); } }, 'Evidence'),
        x.seen ? null : el('button', { onclick: async () => { await api('/api/alerts/seen', { method: 'POST', body: { ids: [x.id] } }); c.classList.add('seen'); refreshBell(); } }, 'Mark seen')));
    return c;
  }
  async function openInbox() {
    const a = await api('/api/alerts');
    const groups = [['HIGH', 'Critical'], ['MED', 'Watch'], ['LOW', 'Info']];
    panel('Alerts', el('p', { class: 'small muted' }, `From your records at revision ${a.revision}. Critical alerts need a decision today; watch items change what you order this week.`),
      el('div', { class: 'chips' }, el('button', { onclick: async () => { await api('/api/alerts/seen', { method: 'POST', body: { ids: a.alerts.map(x => x.id) } }); openInbox(); refreshBell(); } }, 'Mark all seen')),
      ...groups.flatMap(([sev, label]) => { const xs = a.alerts.filter(x => x.severity === sev); return xs.length ? [el('h3', {}, `${label} (${xs.length})`), ...xs.map(x => alertCard(x))] : []; }));
    refreshBell();
    if (tour && !tour.done.alerts) markTour('alerts');
  }

  // ---------- memory (client view) ----------
  function beliefEditor(x, onSaved) {
    const keys = Object.keys(x.value || {});
    const single = keys.length === 1;
    const input = single ? el('input', { value: String(x.value[keys[0]]), style: 'width:160px' }) : el('textarea', {}, JSON.stringify(x.value));
    const box = el('div', { class: 'row hidden' }, single ? el('span', { class: 'small muted' }, keys[0]) : null, input,
      el('button', { onclick: async () => {
        let value;
        try { value = single ? { [keys[0]]: (typeof x.value[keys[0]] === 'number' ? Number(input.value) : input.value) } : JSON.parse(input.value); } catch { toast('not valid JSON'); return; }
        try { const r = await api('/api/beliefs', { method: 'POST', body: { kind: x.kind, subject: x.subject, statement: `Changed in Memory: ${x.subject} = ${JSON.stringify(value)}`, value } }); toast(`remembered (${r.action})`); onSaved && onSaved(); } catch (e) { toast(e.message); }
      } }, 'Save'));
    return [el('button', { class: 'ghost small', onclick: () => box.classList.toggle('hidden') }, 'change'), box];
  }
  function memoryForm(prefill, onSaved) {
    const subjects = ['order.buffer_days', 'order.brunch_uplift', 'order.cover_through', 'order.max_unflagged_usd', 'invoices.price_anomaly_ratio', 'inbound.on_hand_rule', 'sources.precedence', 'recipes.missing_item_rule', 'order.sku.ING-CHICKEN', 'invoice.INV-099.physically_received', 'vendor.V1.delivery_days'];
    const kind = el('select', {}, ...['preference', 'policy', 'assertion'].map(k => el('option', { value: k }, k)));
    const subject = el('input', { list: 'subjects', placeholder: 'subject, e.g. order.buffer_days', style: 'width:220px' });
    const statement = el('input', { value: prefill || '', placeholder: 'in your words', style: 'width:100%' });
    const value = el('input', { placeholder: 'value as JSON, e.g. {"days": 2}', style: 'width:220px' });
    const dl = el('datalist', { id: 'subjects' }, ...subjects.map(sb => el('option', { value: sb })));
    const form = el('div', { class: 'card', style: 'margin-top:6px' }, el('div', { class: 'small muted' }, 'Add to memory (recorded under your name, source human:web)'),
      el('div', { class: 'row' }, kind, subject, dl), el('div', { class: 'row' }, statement), el('div', { class: 'row' }, value,
        el('button', { onclick: async () => { let v; try { v = JSON.parse(value.value); } catch { toast('value must be JSON like {"days": 2}'); return; } try { const r = await api('/api/beliefs', { method: 'POST', body: { kind: kind.value, subject: subject.value.trim(), statement: statement.value.trim() || subject.value, value: v } }); form.replaceChildren(el('span', { class: 'chip on' }, `remembered [${r.action}]: ${r.recorded.subject} = ${JSON.stringify(r.recorded.value)}`)); onSaved && onSaved(); } catch (e) { toast(e.message); } } }, 'Remember'),
        el('button', { class: 'ghost', onclick: () => form.remove() }, 'cancel')));
    return form;
  }
  async function openMemory() {
    const b = await api('/api/beliefs');
    const active = b.beliefs.filter(x => x.status === 'active');
    const group = (title, xs, note, editable) => el('div', { class: 'memgroup' }, el('h4', {}, title), note ? el('p', { class: 'small muted' }, note) : null,
      ...(xs.length ? xs.map(x => el('div', { class: 'memrow' }, el('div', { class: 'st' }, x.statement), el('div', { class: 'meta' }, el('span', { class: 'v' }, JSON.stringify(x.value)), ...(editable ? beliefEditor(x, openMemory) : []), el('span', { class: 'src' }, x.source.startsWith('human:') ? 'you said so' : `from ${x.source}`)))) : [el('p', { class: 'muted small' }, 'nothing yet')]));
    panel('Memory',
      el('p', { class: 'small muted' }, 'What I keep about how you run the kitchen. Rules and preferences change only when you say so. Facts from your records are rechecked whenever the data changes.'),
      b.conflicts.length ? el('div', { class: 'card gate' }, el('strong', {}, 'Needs you'), ...b.conflicts.map(c => el('p', { class: 'small' }, `${c.old.subject}: you said ${JSON.stringify(c.old.value)}; the record now says ${c.new ? JSON.stringify(c.new.value) : '?'}. Tell me which is right in chat.`))) : null,
      group('Your rules', active.filter(x => x.kind === 'policy'), 'Change one here, or tell me in chat.', true),
      group('Your preferences', active.filter(x => x.kind === 'preference'), 'Say "use two days of buffer from now on" in chat, or change one here.', true),
      group('Facts I am tracking', active.filter(x => x.kind === 'assertion'), '"you said so" is your word; a note or invoice id means I read it there and will recheck it.', true),
      el('div', { class: 'chips' }, el('button', { onclick: (e) => e.target.parentNode.after(memoryForm('', openMemory)) }, 'Add something to memory')),
      el('p', { class: 'small muted' }, `${active.filter(x => x.kind === 'observation' && !(x.value || {}).cleared).length} current observations from the detectors are rechecked every run.`),
      el('div', { class: 'chips' }, el('button', { onclick: () => { closePanels(); L.setDev(true); location.hash = '#/memory'; } }, 'Full memory (developer view)')));
    if (tour && !tour.done.memory) markTour('memory');
  }

  // ---------- order wizard ----------
  const linesTable = (lines, editable, onChange) => el('table', {}, el('thead', {}, el('tr', {}, ...['ingredient', 'SKU', 'pack', 'cases', 'why'].map(h => el('th', {}, h)))),
    el('tbody', {}, ...lines.map((l, i) => el('tr', { class: l.quantity === 0 ? 'dropped' : '' }, el('td', {}, l.ingredient || ''), el('td', {}, el('strong', {}, l.sku), el('div', { class: 'small muted' }, l.description || '')), el('td', {}, l.pack || `${l.pack_quantity || ''} ${l.pack_unit || ''}`),
      el('td', {}, editable ? el('input', { type: 'number', min: 0, max: 100, value: String(l.quantity), style: 'width:64px', onchange: (e) => onChange(i, Number(e.target.value)) }) : el('strong', {}, String(l.quantity))),
      el('td', { class: 'small' }, (l.notes || []).join('; ') || (l.need ? `need ${Number(l.need).toFixed(1)}, on hand ${l.on_hand}` : ''))))));
  // One wizard per vendor, kept when the panel closes so nothing in progress is lost; links in the chat reopen it.
  const wizards = {};
  const DRAFT_STATUS = { drafted: ['drafted', 'MED'], awaiting_approval: ['awaiting approval', 'MED'], approved: ['approved, submitting', 'MED'],
    submitting: ['result unknown', 'HIGH'], unknown: ['result unknown', 'HIGH'], accepted: ['accepted', 'ok'], failed: ['failed', 'err'], cancelled: ['cancelled', 'LOW'] };
  const statusOf = (s) => DRAFT_STATUS[s] || [String(s || '').replace(/_/g, ' '), 'LOW'];
  const OPEN_STATUSES = ['drafted', 'awaiting_approval', 'approved', 'submitting', 'unknown'];
  const when = (iso) => (iso || '').slice(0, 16).replace('T', ' ');
  // every "open your order" link in the chat shows where that order stands
  function syncLinks(vendor) {
    const w = wizards[vendor];
    for (const b of document.querySelectorAll(`[data-orderlink="${vendor}"]`)) b.textContent = w ? `Open your ${w.name} order · ${w.state}` : `Open the ${vendor} order`;
  }
  const orderLink = (vendor) => { const b = el('button', { 'data-orderlink': vendor, onclick: () => openWizard(vendor, 'reopen') }, 'Open the order panel'); setTimeout(() => syncLinks(vendor)); return b; };
  // mode: '' reopens an unfinished wizard or builds one; 'reopen' brings back the panel as it is, finished or not;
  // 'fresh' starts a new proposal even when a draft is open.
  async function openWizard(vendor = 'V1', mode = '') {
    const existing = wizards[vendor];
    if (existing && mode !== 'fresh' && (mode === 'reopen' || !existing.finished)) { closePanels(); document.body.append(existing.node); return; }
    if (existing && existing.poll) clearInterval(existing.poll);
    const w = wizards[vendor] = { vendor, name: vendor, state: 'opening', finished: false, poll: null, node: null };
    const setState = (state) => { w.state = state; syncLinks(vendor); };
    const steps = el('div', {}); const orders = el('div', { class: 'orders' }); const body = el('div', {});
    const setStep = (n) => { steps.replaceChildren(...['Review the proposal', 'Create the draft and get approval', 'Outcome'].map((t, i) => el('div', { class: `step ${i + 1 === n ? 'on' : i + 1 < n ? 'done' : ''}` }, el('div', { class: 'n' }, String(i + 1)), el('span', {}, t)))); };
    w.node = panel('Your next order', steps, orders, body);
    setStep(1); body.replaceChildren(el('p', { class: 'muted' }, 'sizing from your counts, recipes and rules…'));
    let p, drafts;
    try { [p, drafts] = await Promise.all([api(`/api/order/proposal?vendor=${encodeURIComponent(vendor)}`), api('/api/order/drafts')]); }
    catch (e) { body.replaceChildren(el('p', {}, e.message)); setState('could not load'); return; }
    w.name = (p.vendor_name || vendor).split(' ')[0];
    const mine = drafts.drafts.filter(d => d.vendor_id === vendor);
    renderOrders(mine);
    const open = mine.find(d => OPEN_STATUSES.includes(d.status));
    if (open && mode !== 'fresh') { if (['submitting', 'unknown'].includes(open.status)) showUnknown(open); else watch(open.draft_id); return; }
    showProposal();

    // ----- your orders: every draft for this vendor with its status -----
    function renderOrders(list) {
      if (!list.length) { orders.replaceChildren(); return; }
      orders.replaceChildren(el('h4', {}, `Your ${w.name} orders`), ...list.map(d => {
        const [label, cls] = statusOf(d.status);
        const act = ['drafted', 'awaiting_approval', 'approved'].includes(d.status) ? el('button', { onclick: () => watch(d.draft_id) }, 'Resume')
          : ['submitting', 'unknown'].includes(d.status) ? el('button', { onclick: () => showUnknown(d) }, 'Resolve')
          : d.status === 'accepted' ? el('button', { class: 'ghost', onclick: () => showOutcome(d) }, 'Outcome') : null;
        return el('div', { class: `alertcard ${cls === 'HIGH' ? 'HIGH' : cls === 'MED' ? 'MED' : 'LOW'}` },
          el('div', { class: 'row' }, el('span', { class: `badge ${cls}` }, label), el('strong', {}, d.draft_id), d.order_id ? el('span', { class: 'small muted' }, `→ ${d.order_id}`) : null, el('span', { class: 'spacer' }), act),
          el('p', { class: 'small' }, d.lines.map(l => `${l.quantity} × ${l.sku}${l.ingredient ? ' (' + l.ingredient + ')' : ''}`).join(', ')),
          el('p', { class: 'small muted' }, `${d.status === 'accepted' ? 'accepted' : 'updated'} ${when(d.updated_at)}${d.note ? ' · ' + d.note : ''}${d.last_error && !d.note ? ' · ' + d.last_error : ''}`));
      }), el('div', { class: 'chips' }, el('button', { class: 'ghost', onclick: () => openWizard(vendor, 'fresh') }, 'Start a new proposal')));
    }
    async function refreshOrders() { try { const d = await api('/api/order/drafts'); renderOrders(d.drafts.filter(x => x.vendor_id === vendor)); } catch (e) { console.error(e); } }

    // ----- step 1: the proposal, net of what accepted orders already cover -----
    function showProposal() {
      setStep(1); setState('reviewing the proposal'); w.finished = false;
      if (w.poll) { clearInterval(w.poll); w.poll = null; }
      if (!p.lines.length) { body.replaceChildren(el('p', {}, `Nothing to order from ${p.vendor_name || vendor} for the coverage window.`), el('pre', { class: 'block small' }, p.rationale)); return; }
      const avail = p.lines.filter(l => l.remaining > 0);
      if (!avail.length) {
        const ids = p.accepted.map(a => a.order_id || a.draft_id).join(', ');
        body.replaceChildren(el('p', {}, `Your accepted order${p.accepted.length === 1 ? '' : 's'} (${ids}) already cover${p.accepted.length === 1 ? 's' : ''} this window for ${p.vendor_name || vendor}: ${Object.entries(p.covered).map(([sku, q]) => `${q} × ${sku}`).join(', ')}. Nothing more to draft.`),
          el('details', {}, el('summary', { class: 'small' }, 'full rationale'), el('pre', { class: 'block small' }, p.rationale)));
        return;
      }
      // the lines the draft will contain; the manager can change them before anything is drafted
      const build = () => avail.map(l => ({ sku: l.sku, quantity: l.remaining, unit: l.order_unit, ingredient: l.ingredient, description: l.sku, pack: `${l.pack_quantity} ${l.pack_unit}`,
        notes: l.already_ordered ? [...(l.notes || []), `${l.already_ordered} already ordered, ${l.cases} proposed in all`] : l.notes, need: l.need, on_hand: l.on_hand }));
      let lines = build();
      const original = lines.map(l => ({ sku: l.sku, quantity: l.quantity }));
      const tableBox = el('div', {}); const changes = el('div', { class: 'small' }); const explain = el('div', {});
      const render = () => { tableBox.replaceChildren(linesTable(lines, true, (i, q) => { lines[i].quantity = q; render(); })); const d = []; for (const o of original) { const n = lines.find(l => l.sku === o.sku); if (!n || n.quantity === 0) d.push(`drop ${o.sku}`); else if (n.quantity !== o.quantity) d.push(`${o.sku}: ${o.quantity} → ${n.quantity}`); } for (const n of lines) if (!original.find(o => o.sku === n.sku) && n.quantity > 0) d.push(`add ${n.sku} ×${n.quantity}`); changes.replaceChildren(d.length ? el('span', {}, el('strong', {}, 'Your changes: '), d.join('; '), ' ', el('button', { class: 'ghost small', onclick: () => { lines = build(); render(); } }, 'reset')) : el('span', { class: 'muted' }, 'as proposed')); setState(d.length ? 'proposal with your edits' : 'reviewing the proposal'); };
      render();
      const req = el('input', { placeholder: 'ask for a change in words, e.g. "make the chicken 6 cases and drop the cheese"', style: 'flex:1' });
      const cal = p.calendar;
      body.replaceChildren(...kids(
        el('p', {}, `${cal.delivery_date ? `Delivery ${cal.delivery_date}` : 'Next delivery'}${cal.order_by ? `, order by ${when(cal.order_by)}` : ''}. Covers through ${cal.window_end} plus ${cal.buffer_days} buffer day(s). Nothing is sent until your operator approves it.`),
        p.accepted.length ? el('p', { class: 'small' }, el('strong', {}, 'Already ordered: '), `${Object.entries(p.covered).map(([sku, q]) => `${q} × ${sku}`).join(', ')} (${p.accepted.map(a => a.order_id || a.draft_id).join(', ')}); the lines below are what is still needed.`) : null,
        tableBox, changes,
        el('div', { class: 'row' }, req, el('button', { class: 'ghost', onclick: () => revise() }, 'Ask Marbly to change it')),
        el('p', { class: 'small muted' }, 'Edit the cases directly, or describe the change and Marbly proposes revised lines; the catalog check runs either way and nothing is drafted until you say so. Close this panel any time; the link in the chat brings it back as you left it.'),
        p.not_counted.length ? el('p', { class: 'small' }, el('strong', {}, 'Not counted: '), p.not_counted.join(' ')) : null,
        p.warnings.length ? el('div', {}, ...p.warnings.map(w => el('p', { class: 'small' }, el('strong', {}, 'Heads up: '), w))) : null,
        el('p', { class: 'small' }, `Estimated ${p.est_total_usd ? '$' + Number(p.est_total_usd).toFixed(2) : 'n/a'} at last invoiced prices for the proposal as sized${p.over_max ? ' (above your unflagged limit)' : ''}. Rules applied: ${p.applied.map(a => a.split(' (')[0]).join(', ')}.`),
        el('div', { class: 'chips' }, el('button', { onclick: () => createDraft() }, 'Create the draft'), el('button', { onclick: () => explainIt() }, 'Explain this to me'), el('details', {}, el('summary', { class: 'small' }, 'full rationale'), el('pre', { class: 'block small' }, p.rationale))),
        explain));
      async function revise() {
        const text = req.value.trim(); if (!text) { toast('say what to change'); return; }
        explain.replaceChildren(el('p', { class: 'muted' }, 'Marbly is revising the lines…')); think(true);
        try {
          const r = await api('/api/order/revise', { method: 'POST', body: { vendor, request: text, lines: lines.filter(l => l.quantity > 0).map(l => ({ sku: l.sku, quantity: l.quantity })) } });
          lines = r.lines.map(l => ({ ...l, notes: ['revised on your request'] })); render();
          explain.replaceChildren(el('div', { class: 'bubble m' }, el('div', { class: 'who' }, avatar(22), ' Marbly'), `${r.note} (${r.diff.join('; ')})`)); req.value = '';
        } catch (e) { explain.replaceChildren(el('p', { class: 'small' }, e.message)); }
        think(false);
      }
      async function explainIt() {
        explain.replaceChildren(el('p', { class: 'muted' }, 'Marbly is reading the proposal…')); think(true);
        await stream('/api/order/explain', { vendor }, (ev) => { if (ev.type === 'done') explain.replaceChildren(el('div', { class: 'bubble m' }, el('div', { class: 'who' }, avatar(22), ' Marbly'), linkify(ev.text || ev.note || '', ev.citations))); if (ev.type === 'error') explain.replaceChildren(el('p', { class: 'small' }, ev.message)); });
        think(false);
      }
      async function createDraft() {
        const final = lines.filter(l => l.quantity > 0).map(l => ({ sku: l.sku, quantity: l.quantity }));
        const edited = JSON.stringify(final) !== JSON.stringify(original);
        setStep(2); setState('creating the draft');
        let r;
        try { r = await api('/api/order/draft', { method: 'POST', body: edited ? { vendor, lines: final } : { vendor } }); }
        catch (e) { setStep(1); setState('draft refused'); body.append(el('p', { class: 'small bad' }, e.message)); refreshOrders(); return; }
        refreshOrders();
        poll(r.draft.draft_id, showDraft(r));
      }
    }

    // ----- step 2: the draft as the supplier holds it, and how the operator approves it -----
    function showDraft(r) {
      const d = r.draft;
      setStep(2); setState(`${d.draft_id} ${statusOf(r.server_status || d.status)[0]}`);
      const status = el('p', {}, 'Waiting for the operator…');
      const cmd = el('pre', { class: 'block' }, r.approve_command);
      body.replaceChildren(...kids(
        el('p', {}, `Draft ${d.draft_id} is with the supplier, ${statusOf(r.server_status || d.status)[0]}${r.edited && r.edited.length ? ' (your edits: ' + r.edited.join('; ') + ')' : ''}. The exact order:`),
        linesTable(d.lines, false),
        el('details', {}, el('summary', { class: 'small' }, 'exact payload the supplier holds'), el('pre', { class: 'block small' }, JSON.stringify(d.order, null, 1))),
        d.note && !r.edited ? el('p', { class: 'small muted' }, d.note) : null,
        el('h4', {}, 'How to approve'), el('p', { class: 'small' }, r.how_to),
        el('div', { class: 'row' }, cmd, el('button', { class: 'ghost', onclick: () => { navigator.clipboard?.writeText(r.approve_command); toast('copied'); } }, 'Copy')),
        status,
        el('p', { class: 'small muted' }, 'You can close this panel; I keep watching for the approval, and the link in the chat brings you back here.')));
      return status;
    }
    async function watch(draftId) {
      let r;
      try { r = await api(`/api/order/${draftId}`); } catch (e) { body.replaceChildren(el('p', { class: 'small bad' }, e.message)); return; }
      poll(draftId, showDraft(r));
    }
    function poll(draftId, status) {
      if (w.poll) clearInterval(w.poll);
      const tick = async () => {
        try {
          const s = await api(`/api/order/${draftId}`);
          if (s.server_status !== 'approved') return;
          clearInterval(w.poll); w.poll = null; status.textContent = 'Approved. Submitting…'; setState(`${draftId} approved, submitting`);
          const o = await api(`/api/order/${draftId}/submit`, { method: 'POST' });
          showOutcome({ draft_id: draftId, status: o.state, order_id: o.order_id, note: o.message }, o);
          refreshOrders(); refreshBell();
          if (!w.node.isConnected) say('', 'Marbly', el('div', {}, el('p', {}, `Your ${w.name} draft ${draftId} was approved and ${o.state === 'accepted' ? 'accepted as ' + o.order_id : 'came back ' + statusOf(o.state)[0]}.`), el('div', { class: 'chips' }, orderLink(vendor))));
          const before = tour && tour.current;
          await refreshTour();
          if (before === 'order' && tour && tour.complete) { nextStepMessage(); afterFirstOrder(); }
        } catch (e) { status.textContent = e.message; }
      };
      w.poll = setInterval(tick, 2000); tick();
    }
    // a draft whose submission result was lost: resolve it from the supplier's records, never by resubmitting blindly
    function showUnknown(d) {
      setStep(2); setState(`${d.draft_id} result unknown`);
      const status = el('p', {}, '');
      body.replaceChildren(el('p', {}, `The supplier's answer to draft ${d.draft_id} was lost, so I do not know whether it went through.${d.note ? ' ' + d.note : ''} I can look it up by its reference; nothing is resubmitted until I know.`),
        linesTable(d.lines, false),
        el('div', { class: 'chips' }, el('button', { onclick: async () => { status.textContent = 'checking with the supplier…'; try { const r = await api('/api/order/resume', { method: 'POST' }); const o = r.outcomes[0]; const fresh = (await api('/api/order/drafts')).drafts.find(x => x.draft_id === d.draft_id) || d; renderOrders((await api('/api/order/drafts')).drafts.filter(x => x.vendor_id === vendor)); if (o) showOutcome(fresh, o); else status.textContent = 'nothing to resolve'; } catch (e) { status.textContent = e.message; } } }, 'Resolve it now'), el('button', { class: 'ghost', onclick: () => showProposal() }, 'Start a new proposal')),
        status);
    }

    // ----- step 3: the outcome, from the supplier's records -----
    function showOutcome(d, o = null) {
      setStep(3); w.finished = true; if (w.poll) { clearInterval(w.poll); w.poll = null; }
      const state = o ? o.state : d.status; const ok = state === 'accepted'; const orderId = (o && o.order_id) || d.order_id;
      setState(`${d.draft_id} ${statusOf(state)[0]}${ok && orderId ? ' as ' + orderId : ''}`);
      body.replaceChildren(el('div', { class: `alertcard ${ok ? 'LOW' : 'HIGH'}` }, el('div', { class: 'row' }, el('span', { class: 'badge ' + (ok ? 'ok' : 'err') }, statusOf(state)[0]), el('strong', {}, d.draft_id), orderId ? el('span', { class: 'small muted' }, `→ ${orderId}`) : null), el('p', {}, o ? o.message : d.note || `${d.draft_id} is ${statusOf(state)[0]}.`)),
        el('div', { class: 'chips' }, el('button', { onclick: () => { closePanels(); sendText(`Did the ${w.name} order go through?`); } }, 'Ask Marbly to confirm'), el('button', { onclick: () => { closePanels(); openPlanner(); } }, "What's next to order"), el('button', { class: 'ghost', onclick: () => openWizard(vendor, 'fresh') }, 'Start a new proposal'), el('button', { onclick: closePanels }, 'Done')));
    }
  }

  // ---------- planner: what to order next, and when ----------
  const urgencyLabel = { overdue: 'cutoff passed', today: 'order today', 'this week': 'this week', ordered: 'ordered', 'nothing needed': 'nothing needed' };
  async function openPlanner() {
    const d = await api('/api/order/planner');
    panel('What to order next', el('p', { class: 'small muted' }, `Sized from your counts, recipes and rules at revision ${d.revision}; the time is ${d.now.slice(0, 16).replace('T', ' ')}.`),
      ...d.vendors.map(v => el('div', { class: `alertcard ${v.urgency === 'overdue' || v.urgency === 'today' ? 'HIGH' : v.urgency === 'this week' ? 'MED' : 'LOW'}` },
        el('div', { class: 'row' }, el('span', { class: 'badge ' + (v.urgency === 'overdue' || v.urgency === 'today' ? 'HIGH' : v.urgency === 'this week' ? 'MED' : 'ok') }, urgencyLabel[v.urgency] || v.urgency), el('strong', {}, v.vendor_name)),
        el('p', { class: 'small' }, v.delivery_date ? `Next feasible delivery ${v.delivery_date}${v.order_by ? ', order by ' + v.order_by.slice(0, 16).replace('T', ' ') : ''}${v.hours_to_cutoff !== null ? ' (' + (v.hours_to_cutoff < 0 ? 'cutoff passed' : Math.round(v.hours_to_cutoff) + ' h left') + ')' : ''}.` : 'No delivery day known.'),
        v.runs_out_before_delivery.length ? el('p', { class: 'small' }, el('strong', {}, 'Runs out before it arrives: '), v.runs_out_before_delivery.map(r => `${r.ingredient} (${r.days} days)`).join(', ')) : null,
        v.lines.length ? el('p', { class: 'small' }, el('strong', {}, v.accepted.length ? 'Still to order: ' : 'Proposed: '), v.lines.map(l => `${l.cases} × ${l.sku} (${l.ingredient})${l.already_ordered ? ', ' + l.already_ordered + ' already ordered' : ''}`).join(', '), v.est_total_usd && !v.accepted.length ? ` · est. $${Number(v.est_total_usd).toFixed(2)}` : '') : el('p', { class: 'small muted' }, v.accepted.length ? 'Covered by the accepted order.' : 'Stock covers the window; nothing to order.'),
        v.accepted.length ? el('p', { class: 'small' }, el('strong', {}, 'Already ordered: '), v.accepted.map(a => `${a.draft_id} as ${a.order_id} (${a.lines.map(l => l.quantity + '×' + l.sku).join(', ')})`).join('; ')) : null,
        v.open_draft ? el('p', { class: 'small' }, el('strong', {}, 'Open draft: '), `${v.open_draft.draft_id} is ${v.open_draft.status.replace('_', ' ')}`) : null,
        v.warnings.length ? el('p', { class: 'small muted' }, v.warnings[0]) : null,
        el('div', { class: 'chips' }, v.lines.length ? el('button', { onclick: () => openWizard(v.vendor_id) }, `Plan the ${v.vendor_name.split(' ')[0]} order`) : null, el('button', { class: 'ghost', onclick: () => { closePanels(); sendText(`What should I order from ${v.vendor_name} and when?`); } }, 'Ask Marbly')))));
  }
  async function afterFirstOrder() {
    try {
      const d = await api('/api/order/planner');
      const next = d.vendors.filter(v => v.lines.length && v.urgency !== 'ordered');
      say('', 'Marbly', el('div', {}, el('p', {}, "That's your first order through. Here is what matters next:"),
        el('ul', {}, ...d.vendors.map(v => el('li', {}, el('strong', {}, v.vendor_name), `: ${urgencyLabel[v.urgency] || v.urgency}` + (v.delivery_date ? `, delivery ${v.delivery_date}, order by ${v.order_by ? v.order_by.slice(0, 16).replace('T', ' ') : '—'}` : '') + (v.runs_out_before_delivery.length ? `; runs out first: ${v.runs_out_before_delivery.map(r => r.ingredient).join(', ')}` : '')))),
        el('div', { class: 'chips' }, el('button', { onclick: openPlanner }, 'Open the planner'), ...next.slice(0, 2).map(v => el('button', { onclick: () => openWizard(v.vendor_id) }, `Plan ${v.vendor_name.split(' ')[0]}`)))));
      dimSpot(document.querySelector('.iconbtn[aria-label=planner]'), 'The planner shows every vendor, in order of cutoff.');
    } catch (e) { console.error(e); }
  }

  // ---------- settings ----------
  function openSettings() {
    const dev = (() => { try { return localStorage.getItem('ly_dev') === '1'; } catch { return false; } })();
    panel('Settings',
      el('div', { class: 'kv' }, el('div', {}, 'restaurant'), el('div', {}, intro.site), el('div', {}, 'data revision'), el('div', {}, String(intro.revision)),
        el('div', {}, 'model'), el('div', {}, intro.dry ? 'off (--dry)' : intro.key_present ? intro.model : 'no key'), el('div', {}, 'memory'), el('div', {}, `${intro.beliefs.policy} rules · ${intro.beliefs.preference} preferences · ${intro.beliefs.assertion} facts`)),
      el('h3', {}, 'Developer view'), el('p', { class: 'small muted' }, 'Shows the chat with tool timeline, full memory, alerts with the revision diff, the order lab, the tool matrix, the injection lab and the diagrams.'),
      el('div', { class: 'chips' }, el('button', { onclick: () => { L.setDev(!dev); openSettings(); } }, dev ? 'Hide developer pages' : 'Show developer pages'), dev ? el('button', { onclick: () => { closePanels(); location.hash = '#/chat'; } }, 'Open developer chat') : null),
      el('h3', {}, 'Memory and setup'),
      el('p', { class: 'small muted' }, 'Memory lives in the ledger on disk and survives every restart. Only the second button below removes it.'),
      el('div', { class: 'chips' },
        el('button', { title: 'Re-ask the six questions. Your rules stay; a changed answer replaces the old one and the history is kept.', onclick: async () => { await api('/api/onboarding/restart', { method: 'POST' }); closePanels(); location.reload(); } }, 'Ask me the questions again'),
        el('button', { class: 'danger', title: 'Delete the ledger, conversation, usage log and setup progress for this state directory.', onclick: async () => { const name = prompt(`Type the state directory name to make Marbly forget everything (${intro.state_dir_name}). The supplier's own records are untouched.`); if (!name) return; try { await api('/api/reset', { method: 'POST', body: { confirm: name } }); location.reload(); } catch (e) { toast(e.message); } } }, 'Forget everything and start over')),
      el('p', { class: 'small muted' }, 'The supplier side (drafts, orders, scenario flags) belongs to the operator; Marbly never holds that credential. To reset it, in the operator terminal:'),
      el('pre', { class: 'block small' }, `python3 simulator.py scenario --name normal --state <your --state file>   # clears scenario flags, keeps orders\npython3 simulator.py serve --mode agent --state .state/<new-name>.sqlite   # a clean supplier: restart with a new file`),
      el('p', { class: 'small muted' }, `This page talks to ${intro.api_base || 'the simulator'}; the assistant's state directory is ${intro.state_dir_name}.`));
  }

  // ---------- onboarding in chat ----------
  async function runOnboarding() {
    say('', 'Marbly', el('div', {}, el('p', {}, intro.greeting), el('p', {}, "Here is how we'll get set up, in four steps you can follow at the top:"),
      el('ol', { class: 'overview' }, el('li', {}, el('strong', {}, 'The big questions.'), ' A few places where your records disagree; your answers become rules I keep.'), el('li', {}, el('strong', {}, "Today's big alerts."), ' What needs a decision, with the evidence behind it.'), el('li', {}, el('strong', {}, 'What I remember, and what I can do.'), ' Your memory, the brief, questions, and the chips to try.'), el('li', {}, el('strong', {}, 'Your first order.'), ' Sized from your numbers and rules, approved by you, verified with the supplier.'))));
    const go = el('div', { class: 'chips' }, el('button', { onclick: () => { go.replaceChildren(el('span', { class: 'chip on' }, "Let's go")); start(); } }, "Let's go"), el('button', { class: 'ghost', onclick: () => { go.replaceChildren(el('span', { class: 'chip' }, 'later')); say('No problem. The setup card at the top will wait; click "Answer the questions" when you are ready.'); } }, 'Later'));
    say('', 'Marbly', el('div', {}, el('p', {}, 'Ready to start with the questions?'), go));
    let firstAnswer = true;
    async function start() {
      const t = thinkingBubble('reading your records and wording the questions…'); think(true);
      const plan = await api('/api/onboarding/plan?wording=1');
      think(false); t.remove();
      if (!plan.questions.length) { say('Everything I found already has an answer from you. Let me look at the picture.'); return finishOnboarding(); }
      say(`Step 1. ${plan.questions.length} things to settle. Pick the answer that is true for you; "unknown, keep checking" is always fine and I will keep watching.`);
      let i = 0;
      const next = () => {
        if (firstAnswer && i === 1) {
          firstAnswer = false;
          say("Thanks. I'll save that in Memory. You can see my memories in the book (top right), split between your rules, your preferences and the facts I track, and change them whenever they change. From now on I ask permission before storing anything: you get a card to approve, edit or reject.");
          dimSpot(book, 'Your memory lives here. Click to look, or anywhere to continue.');
        }
        if (i >= plan.questions.length) return finishOnboarding();
        askQuestion(plan.questions[i++], next, i, plan.questions.length);
      };
      next();
    }
  }
  function askQuestion(q, done, n, total) {
    const card = el('div', { class: 'qcard' }, el('div', { class: 'small muted' }, `${n} of ${total}`), el('h4', {}, q.title), el('p', {}, q.lead || q.why),
      q.lead ? el('details', {}, el('summary', { class: 'small' }, 'the facts'), el('p', { class: 'small' }, q.why)) : null,
      el('div', { class: 'ev' }, ...q.evidence.slice(0, 4).map((e, i) => el('div', {}, linkify(e, (q.evidence_links || [])[i] || [])))));
    const opts = el('div', { class: 'opts' });
    const choose = async (answer) => {
      for (const b of opts.querySelectorAll('button')) b.disabled = true;
      try {
        const r = await api('/api/onboarding/answer', { method: 'POST', body: { id: q.id, answer } });
        card.append(el('div', { class: 'small' }, el('strong', {}, 'Recorded: '), r.recorded.map(b => `${b.kind} ${b.subject.split('.').slice(-2).join('.')} = ${JSON.stringify(b.value)}${b.action === 'superseded' ? ' (replaced the earlier value)' : ''}`).join('; ') || 'nothing new'), el('div', { class: 'small muted' }, r.option.consequence));
        done();
      } catch (e) { toast(e.message); for (const b of opts.querySelectorAll('button')) b.disabled = false; }
    };
    for (const o of q.options) {
      if (o.key === 'custom' && q.fields) {
        const inputs = q.fields.map(f => el('div', { class: 'row' }, el('span', { class: 'small', style: 'min-width:220px' }, f.prompt), el('input', { value: String(f.default), 'data-key': f.key })));
        const box = el('div', { class: 'hidden' }, ...inputs, el('button', { onclick: () => { const vals = {}; for (const inp of box.querySelectorAll('input')) vals[inp.dataset.key] = isNaN(Number(inp.value)) ? inp.value : Number(inp.value); choose(vals); } }, 'Use these values'));
        opts.append(el('button', { class: o.key === q.default ? 'def' : '', onclick: () => box.classList.toggle('hidden') }, o.label), box);
      } else opts.append(el('button', { class: o.key === q.default ? 'def' : '', onclick: () => choose(o.key) }, o.label + (o.key === q.default ? '  (default)' : '')));
    }
    if (q.custom) { const inp = el('input', { placeholder: q.custom.prompt, style: 'width:60%' }); opts.append(el('div', { class: 'row' }, inp, el('button', { class: 'ghost', onclick: () => { const v = Number(inp.value); if (isNaN(v)) { toast('a number, please'); return; } choose(v); } }, 'Use this number'))); }
    card.append(opts);
    say('', 'Marbly', card);
  }
  async function finishOnboarding() {
    const t = thinkingBubble('re-running every check with your answers applied: rules, thresholds, the on-hand policy…'); think(true);
    const shown = sleep(1500);  // keep the thinking state visible even when the recheck is instant
    const done = await api('/api/onboarding/complete', { method: 'POST' });
    L.refreshStatus();
    await api('/api/brief/dry?kind=onboarding');  // the recheck: findings against what you told me
    const a = await api('/api/alerts');
    await shown;
    think(false); t.remove();
    await refreshTour();
    const fx = done.effect || {};
    const changes = [...(fx.removed || []).map(r => `cleared "${r.title}"`), ...(fx.added || []).map(r => `raised "${r.title}"`), ...(fx.severity_changed || []).map(r => `"${r.title}" moved from ${r.from} to ${r.to}`)];
    say(`Thank you. I re-ran the ${fx.before ?? ''} checks with your answers applied (${(fx.applied || []).length} rules and preferences in play). ${changes.length ? 'Because of what you told me: ' + changes.join('; ') + '.' : 'Your answers confirmed the defaults, so the picture is the same as before but now it rests on your rules.'}`);
    const crit = a.alerts.filter(x => x.severity === 'HIGH');
    say(`Step 2: ${crit.length} alert${crit.length === 1 ? '' : 's'} need${crit.length === 1 ? 's' : ''} a decision today, ${a.counts.MED} to watch and ${a.counts.LOW} for the record. The critical ones:`);
    for (const x of crit.slice(0, 3)) say('', 'Marbly', alertCard(x, true));
    say('Open the bell (pulsing, top right) to see all of them with their evidence; that is where new alerts land from now on.');
    dimSpot(bell, 'Alerts live here. Click to open.');
  }
  function introduceCapabilities() {
    say('', 'Marbly', el('div', {}, el('p', {}, 'Three things I can do for you:'), el('div', { class: 'grid three' }, ...intro.capabilities.map(c => el('div', { class: 'capcard' }, el('h4', {}, c.title), el('p', { class: 'small' }, c.blurb), el('div', { class: 'chips' }, ...c.samples.map(s => el('button', { onclick: () => sendText(s) }, s))))))));
    chips.replaceChildren(...intro.capabilities.flatMap(c => c.samples).map(s => el('button', { onclick: () => sendText(s) }, s)));
    spotlight();
  }

  // ---------- chat ----------
  async function sendText(text) {
    if (pending) return;
    say(text, 'You', null, true);
    const low = text.toLowerCase();
    if (/\bbrief\b/.test(low) && /morning|give me|today|run/.test(low)) return runBrief();
    if (/order/.test(low) && /help|next|make|place|plan/.test(low) && !/did|went|go through|status/.test(low)) {
      const w = wizards.V1;
      say('', 'Marbly', el('div', {}, el('p', {}, w && !w.finished ? `Your ${w.name} order is still open where you left it; here it is again.` : 'Opening your next order. I will show every line and why before anything is drafted. Close the panel any time; this link brings it back as you left it.'), el('div', { class: 'chips' }, orderLink('V1'))));
      return openWizard('V1');
    }
    think(true);
    const out = say('…');
    const gates = el('div', {}); out.append(gates);
    await stream('/api/chat', { text, mode: 'ask' }, (ev) => {
      if (ev.type === 'gate') { gateIntro(out); L.gateCard(ev, gates); }
      else if (ev.type === 'done') { out.replaceChildren(...kids(el('div', { class: 'who' }, avatar(22), ' Marbly'), el('div', {}, linkify(ev.text || '(no answer)', ev.citations)), ev.recorded && ev.recorded.length ? el('div', { class: 'chips' }, ...ev.recorded.map(r => el('span', { class: 'chip on' }, `remembered: ${r.recorded.subject} = ${JSON.stringify(r.recorded.value)}`))) : null, el('div', { class: 'small muted' }, ev.usage ? `${ev.tool_calls.length} lookup${ev.tool_calls.length === 1 ? '' : 's'} · ${usd(ev.usage.usd)}` : ''), el('div', { class: 'memo-row' }, el('button', { class: 'ghost small', onclick: (e) => { e.target.disabled = true; out.append(memoryForm((ev.text || '').slice(0, 140))); } }, '+ add to memory')))); }
      else if (ev.type === 'error') out.replaceChildren(el('div', { class: 'who' }, avatar(22), ' Marbly'), el('div', {}, `I can't reach my model right now (${ev.message}). You can still use the alerts, memory and the order steps.`));
      feed.scrollTop = feed.scrollHeight;
    });
    think(false); refreshBell(); L.refreshStatus();
  }
  const gateIntro = (out) => { if (!out.dataset.gi) { out.dataset.gi = '1'; out.append(el('p', { class: 'small muted' }, 'I would like to remember this. Approve, change the value, or reject:')); } };
  async function runBrief() {
    think(true);
    const out = say('…');
    await stream('/api/brief', {}, (ev) => {
      if (ev.type === 'done') out.replaceChildren(...kids(el('div', { class: 'who' }, avatar(22), ' Marbly · morning brief'), el('div', {}, linkify(ev.text || ev.deterministic || '', ev.citations)), ev.text ? null : el('details', {}, el('summary', { class: 'small' }, 'full detail'), el('pre', { class: 'block small' }, ev.footer || '')), el('div', { class: 'small muted' }, ev.usage ? usd(ev.usage.usd) : '')));
      else if (ev.type === 'error') out.append(el('p', { class: 'small' }, ev.message));
      feed.scrollTop = feed.scrollHeight;
    });
    think(false); refreshBell();
  }

  // ---------- page ----------
  window.ClientPage = async () => {
    intro = await api('/api/client/intro');
    intro.state_dir_name = (document.querySelector('#status')?.textContent || '').trim().split(' ').pop().split('/').pop() || '.marble';
    feed = el('div', { class: 'feed' }); chips = el('div', { class: 'chips' }); tourBox = el('div', { class: 'tour hidden' });
    headAvatar = avatar(56);
    bell = el('button', { class: 'iconbtn', 'aria-label': 'alerts', title: 'alerts', onclick: openInbox }, '🔔');
    book = el('button', { class: 'iconbtn', 'aria-label': 'memory', title: 'memory', onclick: openMemory }, '📒');
    const plannerBtn = el('button', { class: 'iconbtn', 'aria-label': 'planner', title: 'what to order next', onclick: openPlanner }, '🗓');
    const input = el('textarea', { placeholder: 'Ask Marbly, or tell it a rule to remember' });
    const send = el('button', { onclick: () => { const t = input.value.trim(); if (!t) return; input.value = ''; sendText(t); } }, 'Send');
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send.click(); } });
    main.replaceChildren(el('div', { class: 'client' },
      el('div', { class: 'client-head' }, headAvatar, el('div', {}, el('h1', {}, 'Marbly'), el('div', { class: 'sub' }, `Lantern Yard Kitchen · ${intro.now.slice(0, 10)}`)), el('div', { class: 'spacer' }),
        bell, book, plannerBtn, el('button', { class: 'iconbtn', 'aria-label': 'settings', title: 'settings', onclick: openSettings }, '⚙')),
      tourBox, feed, chips, el('div', { class: 'composer' }, input, send)));
    tour = intro.tour; renderTour();
    refreshBell();
    if (!intro.onboarded_at) return runOnboarding();
    say(intro.welcome_back);
    const a = await api('/api/alerts');
    const crit = a.alerts.filter(x => x.severity === 'HIGH');
    if (crit.length) { say(`${crit.length} critical alert${crit.length === 1 ? '' : 's'} today:`); for (const x of crit.slice(0, 3)) say('', 'Marbly', alertCard(x, true)); }
    introduceCapabilities();
    if (tour && !tour.complete) nextStepMessage();
  };
})();
