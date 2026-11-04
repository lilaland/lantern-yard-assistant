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

  // ---------- feed ----------
  const say = (text, who = 'Marbly', node = null) => {
    const b = el('div', { class: `bubble ${who === 'Marbly' ? 'm' : 'u'}` }, who === 'Marbly' ? el('div', { class: 'who' }, avatar(22), ' Marbly') : el('div', { class: 'who' }, who), node || text);
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
    if (tour.current === 'order') say("Step 4, the big one. Let's place your first order together: I size it from your counts, recipes and rules, show every line and why, and nothing is sent until your operator approves it. Click the pulsing chip or the button above.");
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
  async function openMemory() {
    const b = await api('/api/beliefs');
    const active = b.beliefs.filter(x => x.status === 'active');
    const group = (title, xs, note) => el('div', { class: 'memgroup' }, el('h4', {}, title), note ? el('p', { class: 'small muted' }, note) : null,
      ...(xs.length ? xs.map(x => el('div', { class: 'memrow' }, el('div', { class: 'st' }, x.statement), el('div', { class: 'meta' }, el('span', { class: 'v' }, JSON.stringify(x.value)), el('span', { class: 'src' }, x.source.startsWith('human:') ? 'you said so' : `from ${x.source}`)))) : [el('p', { class: 'muted small' }, 'nothing yet')]));
    panel('Memory',
      el('p', { class: 'small muted' }, 'What I keep about how you run the kitchen. Rules and preferences change only when you say so. Facts from your records are rechecked whenever the data changes.'),
      b.conflicts.length ? el('div', { class: 'card gate' }, el('strong', {}, 'Needs you'), ...b.conflicts.map(c => el('p', { class: 'small' }, `${c.old.subject}: you said ${JSON.stringify(c.old.value)}; the record now says ${c.new ? JSON.stringify(c.new.value) : '?'}. Tell me which is right in chat.`))) : null,
      group('Your rules', active.filter(x => x.kind === 'policy')),
      group('Your preferences', active.filter(x => x.kind === 'preference'), 'Say "use two days of buffer from now on" in chat to change one.'),
      group('Facts I am tracking', active.filter(x => x.kind === 'assertion'), '"you said so" is your word; a note or invoice id means I read it there and will recheck it.'),
      el('p', { class: 'small muted' }, `${active.filter(x => x.kind === 'observation' && !(x.value || {}).cleared).length} current observations from the detectors are rechecked every run.`),
      el('div', { class: 'chips' }, el('button', { onclick: () => { closePanels(); L.setDev(true); location.hash = '#/memory'; } }, 'Full memory (developer view)')));
    if (tour && !tour.done.memory) markTour('memory');
  }

  // ---------- order wizard ----------
  async function openWizard(vendor = 'V1') {
    const steps = el('div', {}); const body = el('div', {});
    const setStep = (n) => { steps.replaceChildren(...['Review the proposal', 'Create the draft and get approval', 'Outcome'].map((t, i) => el('div', { class: `step ${i + 1 === n ? 'on' : i + 1 < n ? 'done' : ''}` }, el('div', { class: 'n' }, String(i + 1)), el('span', {}, t)))); };
    panel('Your next order', steps, body);
    setStep(1); body.replaceChildren(el('p', { class: 'muted' }, 'sizing from your counts, recipes and rules…'));
    let p;
    try { p = await api(`/api/order/proposal?vendor=${encodeURIComponent(vendor)}`); } catch (e) { body.replaceChildren(el('p', {}, e.message)); return; }
    if (!p.lines.length) { body.replaceChildren(el('p', {}, `Nothing to order from ${vendor} for the coverage window.`), el('pre', { class: 'block small' }, p.rationale)); return; }
    const lines = el('table', {}, el('thead', {}, el('tr', {}, ...['ingredient', 'on hand', 'need', 'order', 'why'].map(h => el('th', {}, h)))),
      el('tbody', {}, ...p.lines.map(l => el('tr', {}, el('td', {}, l.ingredient), el('td', {}, `${l.on_hand} ${l.pack_unit}`), el('td', {}, `${Number(l.need).toFixed(1)} ${l.pack_unit}`), el('td', {}, el('strong', {}, `${l.cases} × ${l.sku}`), el('div', { class: 'small muted' }, `${l.pack_quantity} ${l.pack_unit} per ${l.order_unit}`)), el('td', { class: 'small' }, (l.notes || []).join('; '))))));
    const explain = el('div', {});
    const cal = p.calendar;
    body.replaceChildren(...kids(
      el('p', {}, `${cal.delivery_date ? `Delivery ${cal.delivery_date}` : 'Next delivery'}${cal.order_by ? `, order by ${cal.order_by.slice(0, 16).replace('T', ' ')}` : ''}. Covers through ${cal.window_end} plus ${cal.buffer_days} buffer day(s). Nothing is sent until your operator approves it.`),
      lines,
      p.not_counted.length ? el('p', { class: 'small' }, el('strong', {}, 'Not counted: '), p.not_counted.join(' ')) : null,
      p.warnings.length ? el('div', {}, ...p.warnings.map(w => el('p', { class: 'small' }, el('strong', {}, 'Heads up: '), w))) : null,
      el('p', { class: 'small' }, `Estimated ${p.est_total_usd ? '$' + Number(p.est_total_usd).toFixed(2) : 'n/a'} at last invoiced prices${p.over_max ? ' (above your unflagged limit)' : ''}. Rules applied: ${p.applied.map(a => a.split(' (')[0]).join(', ')}.`),
      el('div', { class: 'chips' }, el('button', { onclick: () => createDraft() }, 'Create the draft'), el('button', { onclick: () => explainIt() }, 'Explain this to me'), el('details', {}, el('summary', { class: 'small' }, 'full rationale'), el('pre', { class: 'block small' }, p.rationale))),
      explain));
    async function explainIt() {
      explain.replaceChildren(el('p', { class: 'muted' }, 'Marbly is reading the proposal…')); think(true);
      await stream('/api/order/explain', { vendor }, (ev) => { if (ev.type === 'done') explain.replaceChildren(el('div', { class: 'bubble m' }, el('div', { class: 'who' }, avatar(22), ' Marbly'), linkify(ev.text || ev.note || '', ev.citations))); if (ev.type === 'error') explain.replaceChildren(el('p', { class: 'small' }, ev.message)); });
      think(false);
    }
    async function createDraft() {
      setStep(2);
      let r;
      try { r = await api('/api/order/draft', { method: 'POST', body: { vendor } }); } catch (e) { body.append(el('p', { class: 'small' }, e.message)); return; }
      const status = el('p', {}, 'Waiting for the operator…');
      body.replaceChildren(
        el('p', {}, `Draft ${r.draft.draft_id} is with the supplier, awaiting approval. The exact order:`),
        el('pre', { class: 'block small' }, JSON.stringify(r.draft.order, null, 1)),
        el('p', {}, 'Approval happens outside this screen on purpose. In the operator terminal run:'),
        el('pre', { class: 'block' }, r.approve_command),
        status);
      const poll = setInterval(async () => {
        try {
          const s = await api(`/api/order/${r.draft.draft_id}`);
          if (s.server_status === 'approved') {
            clearInterval(poll); status.textContent = 'Approved. Submitting…';
            const o = await api(`/api/order/${r.draft.draft_id}/submit`, { method: 'POST' });
            setStep(3);
            body.replaceChildren(el('div', { class: `alertcard ${o.state === 'accepted' ? 'LOW' : 'HIGH'}` }, el('div', { class: 'row' }, el('span', { class: 'badge ' + (o.state === 'accepted' ? 'ok' : 'err') }, o.state.replace('_', ' ')), el('strong', {}, r.draft.draft_id)), el('p', {}, o.message)),
              el('div', { class: 'chips' }, el('button', { onclick: () => { closePanels(); sendText('Did the Alder order go through?'); } }, 'Ask Marbly to confirm'), el('button', { onclick: closePanels }, 'Done')));
            refreshBell();
            const before = tour && tour.current;
            await refreshTour();
            if (before === 'order' && tour && tour.complete) nextStepMessage();
          }
        } catch (e) { status.textContent = e.message; }
      }, 2000);
    }
  }

  // ---------- settings ----------
  function openSettings() {
    const dev = (() => { try { return localStorage.getItem('ly_dev') === '1'; } catch { return false; } })();
    panel('Settings',
      el('div', { class: 'kv' }, el('div', {}, 'restaurant'), el('div', {}, intro.site), el('div', {}, 'data revision'), el('div', {}, String(intro.revision)),
        el('div', {}, 'model'), el('div', {}, intro.dry ? 'off (--dry)' : intro.key_present ? intro.model : 'no key'), el('div', {}, 'memory'), el('div', {}, `${intro.beliefs.policy} rules · ${intro.beliefs.preference} preferences · ${intro.beliefs.assertion} facts`)),
      el('h3', {}, 'Developer view'), el('p', { class: 'small muted' }, 'Shows the chat with tool timeline, full memory, alerts with the revision diff, the order lab, the tool matrix, the injection lab and the diagrams.'),
      el('div', { class: 'chips' }, el('button', { onclick: () => { L.setDev(!dev); openSettings(); } }, dev ? 'Hide developer pages' : 'Show developer pages'), dev ? el('button', { onclick: () => { closePanels(); location.hash = '#/chat'; } }, 'Open developer chat') : null),
      el('h3', {}, 'Setup'), el('div', { class: 'chips' }, el('button', { onclick: async () => { await api('/api/onboarding/restart', { method: 'POST' }); closePanels(); location.reload(); } }, 'Run the setup again'), el('button', { class: 'danger', onclick: async () => { const name = prompt(`Type the state directory name to wipe everything Marbly remembers (${intro.state_dir_name}). The supplier's records are untouched.`); if (!name) return; try { await api('/api/reset', { method: 'POST', body: { confirm: name } }); location.reload(); } catch (e) { toast(e.message); } } }, 'Start over (wipe memory)')));
  }

  // ---------- onboarding in chat ----------
  async function runOnboarding() {
    say('', 'Marbly', el('div', {}, el('p', {}, intro.greeting), el('p', {}, "Here is how we'll get set up, in four steps you can follow at the top:"),
      el('ol', { class: 'overview' }, el('li', {}, el('strong', {}, 'The big questions.'), ' A few places where your records disagree; your answers become rules I keep.'), el('li', {}, el('strong', {}, "Today's big alerts."), ' What needs a decision, with the evidence behind it.'), el('li', {}, el('strong', {}, 'What I remember, and what I can do.'), ' Your memory, the brief, questions, and the chips to try.'), el('li', {}, el('strong', {}, 'Your first order.'), ' Sized from your numbers and rules, approved by you, verified with the supplier.'))));
    think(true);
    const plan = await api('/api/onboarding/plan?wording=1');
    think(false);
    if (!plan.questions.length) { say('Everything I found already has an answer from you. Let me look at the picture.'); return finishOnboarding(); }
    say(`Step 1. ${plan.questions.length} things to settle. Pick the answer that is true for you; "unknown, keep checking" is always fine and I will keep watching.`);
    let i = 0;
    const next = () => { if (i >= plan.questions.length) return finishOnboarding(); askQuestion(plan.questions[i++], next, i, plan.questions.length); };
    next();
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
    await api('/api/onboarding/complete', { method: 'POST' });
    L.refreshStatus();
    think(true);
    await api('/api/brief/dry?kind=onboarding');  // the recheck: findings against what you told me
    const a = await api('/api/alerts');
    think(false);
    await refreshTour();
    const crit = a.alerts.filter(x => x.severity === 'HIGH');
    say(`Thank you. I have rechecked everything against your answers. Step 2: ${crit.length} alert${crit.length === 1 ? '' : 's'} need${crit.length === 1 ? 's' : ''} a decision today, ${a.counts.MED} to watch and ${a.counts.LOW} for the record. The critical ones:`);
    for (const x of crit.slice(0, 3)) say('', 'Marbly', alertCard(x, true));
    say('Open the bell (pulsing, top right) to see all of them with their evidence; that is where new alerts land from now on.');
  }
  function introduceCapabilities() {
    say('', 'Marbly', el('div', {}, el('p', {}, 'Three things I can do for you:'), el('div', { class: 'grid three' }, ...intro.capabilities.map(c => el('div', { class: 'capcard' }, el('h4', {}, c.title), el('p', { class: 'small' }, c.blurb), el('div', { class: 'chips' }, ...c.samples.map(s => el('button', { onclick: () => sendText(s) }, s))))))));
    chips.replaceChildren(...intro.capabilities.flatMap(c => c.samples).map(s => el('button', { onclick: () => sendText(s) }, s)));
    spotlight();
  }

  // ---------- chat ----------
  async function sendText(text) {
    if (pending) return;
    say(text, 'You');
    const low = text.toLowerCase();
    if (/\bbrief\b/.test(low) && /morning|give me|today|run/.test(low)) return runBrief();
    if (/order/.test(low) && /help|next|make|place|plan/.test(low) && !/did|went|go through|status/.test(low)) { say('Opening your next order. I will show every line and why before anything is drafted.'); return openWizard('V1'); }
    think(true);
    const out = say('…');
    const gates = el('div', {}); out.append(gates);
    await stream('/api/chat', { text, mode: 'ask' }, (ev) => {
      if (ev.type === 'gate') { gateIntro(out); L.gateCard(ev, gates); }
      else if (ev.type === 'done') { out.replaceChildren(...kids(el('div', { class: 'who' }, avatar(22), ' Marbly'), el('div', {}, linkify(ev.text || '(no answer)', ev.citations)), ev.recorded && ev.recorded.length ? el('div', { class: 'chips' }, ...ev.recorded.map(r => el('span', { class: 'chip on' }, `remembered: ${r.recorded.subject} = ${JSON.stringify(r.recorded.value)}`))) : null, el('div', { class: 'small muted' }, ev.usage ? `${ev.tool_calls.length} lookup${ev.tool_calls.length === 1 ? '' : 's'} · ${usd(ev.usage.usd)}` : ''))); }
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
    const input = el('textarea', { placeholder: 'Ask Marbly, or tell it a rule to remember' });
    const send = el('button', { onclick: () => { const t = input.value.trim(); if (!t) return; input.value = ''; sendText(t); } }, 'Send');
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send.click(); } });
    main.replaceChildren(el('div', { class: 'client' },
      el('div', { class: 'client-head' }, headAvatar, el('div', {}, el('h1', {}, 'Marbly'), el('div', { class: 'sub' }, `Lantern Yard Kitchen · ${intro.now.slice(0, 10)}`)), el('div', { class: 'spacer' }),
        bell, book, el('button', { class: 'iconbtn', 'aria-label': 'settings', title: 'settings', onclick: openSettings }, '⚙')),
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
