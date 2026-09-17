/*
  DataQC dashboard.

  Served by config_api.py, normally from the same origin as the endpoints it
  calls - see API_BASE below for the case where it is not.

  It talks to these:
      GET    /rules/template            what a new well's form is filled with
      GET    /wells                     which wells are monitored
      POST   /wells                     start one, with its own rules
      GET    /wells/{name}              one well, rules included
      PUT    /wells/{name}/rules        change that well's rules
      DELETE /wells/{name}              stop monitoring one
      GET    /alerts/{name}             what that well's agent has raised
      GET    /rules/display_name        what each parameter is called in alerts
      PUT    /rules/display_name        change those names, for every well

  Rules belong to one well: what is entered for a well affects that well only.

  Cards are built once per well and updated in place afterwards. Re-rendering
  the grid every poll would restart every animation and throw away the user's
  scroll position in a card they were reading.
*/

'use strict';

/*
  Where the API is.

  Served by config_api.py - open http://localhost:8000 - the page and the
  endpoints share an origin and a relative path just works.

  Served from a separate static server while working on the page
  ("python -m http.server 5500", VS Code Live Server) they do not share one.
  That server has no /wells, so every call 404s and POST comes back 501. When
  the page is not on the API's own port, calls are pointed back at it.

  Change API_PORT here if you change CONFIG_API_PORT in .env.
*/
const API_PORT = '8000';

const API_BASE = (() => {
    if (location.protocol === 'file:') {
        return 'http://localhost:' + API_PORT;
    }

    if (location.port === API_PORT || location.port === '') {
        return '';
    }

    return location.protocol + '//' + location.hostname + ':' + API_PORT;
})();

/*
  Every timing and limit below is set in .env and served by GET /settings, so
  this file is not where any of them is changed. What is written here is only
  the fallback used until that call answers, and if it never does - the page
  still works against a server too old to have the endpoint, or one that is
  briefly down, rather than coming up blank.

  settings.load() overwrites these at boot. They are read through the `S`
  object rather than as bare constants so that the values a poll uses are the
  ones in force now, not the ones that happened to be compiled in.
*/
const S = {
    // Milliseconds between polls of /wells and /alerts.
    pollMs: 5000,

    // How long after a well appears to keep saying "starting" rather than
    // "all clear" - the manager takes a few seconds to notice a new well.
    startingMs: 8000,

    // How long an alert stays on its card. The server does the ageing, against
    // the same clock that stamped the alert.
    alertMaxAgeMin: 30,

    // Most alerts a card will hold, newest kept. A backstop for a well raising
    // something new every second, not a window expected to be reached.
    alertLimit: 1000,

    // Resize limits. Height is in pixels of alert list; width is grid columns.
    minListH: 90,
    maxListH: 900,
    maxSpan: 4,
};

/*
  Ask the server what it was configured with.

  Failure is not fatal and is not shown to anyone: the fallbacks above are
  sensible, and a dashboard that refuses to start because it could not read a
  poll interval would be worse than one running five seconds off.
*/
async function loadSettings() {
    try {
        const s = await api('/settings');

        S.pollMs = s.poll_seconds * 1000;
        S.startingMs = s.starting_seconds * 1000;
        S.alertMaxAgeMin = s.alert_max_age_minutes;
        S.alertLimit = s.alert_limit;
        S.minListH = s.card_min_height;
        S.maxListH = s.card_max_height;
        S.maxSpan = s.card_max_columns;
    } catch (err) {
        console.warn('Using built-in defaults; GET /settings failed:', err.message);
    }
}

// Where per-card sizes are remembered between visits.
const SIZE_KEY = 'dataqc.card-sizes';

// ---------------------------------------------------------------------------
// Elements
// ---------------------------------------------------------------------------

const el = {
    grid: document.getElementById('grid'),
    empty: document.getElementById('empty'),
    statWells: document.getElementById('stat-wells'),
    statUpdated: document.getElementById('stat-updated'),
    linkState: document.getElementById('link-state'),
    linkText: document.getElementById('link-text'),
    modal: document.getElementById('modal'),
    form: document.getElementById('add-well-form'),
    dbName: document.getElementById('database_name'),
    ipAddress: document.getElementById('ip_address'),
    submit: document.getElementById('add-submit'),
    modalTitle: document.getElementById('modal-title'),
    modalError: document.getElementById('modal-error'),
    resetDefaults: document.getElementById('reset-defaults'),
    toasts: document.getElementById('toasts'),
    tplWell: document.getElementById('tpl-well'),
    tplAlert: document.getElementById('tpl-alert'),
    modalTabs: document.getElementById('modal-tabs'),
    displayNamesForm: document.getElementById('display-names-form'),
    displayNamesSave: document.getElementById('display-names-save'),
    displayNamesError: document.getElementById('display-names-error'),
};

// database_name -> { card, firstSeen, activity }
const cards = new Map();

let polling = null;

// ---------------------------------------------------------------------------
// Talking to the API
// ---------------------------------------------------------------------------

async function api(path, options) {
    const response = await fetch(API_BASE + path, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });

    let body = null;

    try {
        body = await response.json();
    } catch (err) {
        // A 500 from the server can come back as HTML; the status is enough.
    }

    if (!response.ok) {
        const detail = body && body.detail ? body.detail : 'Request failed';
        throw new Error(typeof detail === 'string' ? detail : 'Request failed');
    }

    return body;
}

// ---------------------------------------------------------------------------
// Reading an alert
//
// The agent writes them as "[11-09-26 17-20-28] SPP dropped by 4.10% where
// BD-104.5" - a timestamp, then the sentence.
// ---------------------------------------------------------------------------

const ALERT_SHAPE = /^\[([^\]]*)\]\s*([\s\S]*)$/;

function parseAlert(raw) {
    const match = ALERT_SHAPE.exec(String(raw));

    if (!match) {
        return { time: '', message: String(raw) };
    }

    // "11-09-26 17-20-28" -> "17:20:28"
    const clock = match[1].split(' ')[1] || '';

    return { time: clock.replace(/-/g, ':'), message: match[2] };
}

/*
  How loudly to say it.

  The row shows the agent's own sentence and nothing else, so this only picks
  the colour of the row's edge - enough to scan a card without reading it.
*/
const KINDS = [
    [/Please check for data Trans/i, 'critical'],
    // TA and TG may carry display names, so only the shape is matched.
    [/Cannot determine activity/i, 'critical'],
    [/Unknown activity/i, 'critical'],
    [/remained unchanged/i, 'warn'],
];

function classify(message) {
    for (const [pattern, tone] of KINDS) {
        if (pattern.test(message)) {
            return { tone };
        }
    }

    // Everything left is a change the agent noticed: SPP, SPM or ROP moving.
    return { tone: 'info' };
}

/*
  The card is a log: every alert on its own line, newest first, nothing merged
  or updated in place. The agent only saves an alert when it says something
  new, so there are no repeats here to collapse.

  Each one gets a key that stays the same from one poll to the next - the raw
  text, which carries its own timestamp, plus a count in case the same text
  was ever saved twice - so the card can add new lines without redrawing the
  ones already on it.
*/
function listAlerts(raw) {
    const seen = new Map();

    const alerts = raw.map((entry) => {
        const text = String(entry);
        const occurrence = (seen.get(text) || 0) + 1;

        seen.set(text, occurrence);

        const { time, message } = parseAlert(text);

        return { key: text + '#' + occurrence, time, message, ...classify(message) };
    });

    return alerts.reverse();
}

const WORST = { critical: 3, warn: 2, info: 1 };

function worstTone(alerts) {
    return alerts.reduce(
        (worst, alert) => (WORST[alert.tone] > WORST[worst] ? alert.tone : worst),
        'info',
    );
}


// ---------------------------------------------------------------------------
// Resizing a card
//
// Cards sit in a CSS grid, so the two directions behave differently on
// purpose. Height is free: the alert list takes whatever pixel height it is
// dragged to. Width moves in whole grid columns, because a card that is some
// arbitrary fraction of a track wide either overflows its neighbour or leaves
// a ragged gap - stepping by columns keeps the wall of cards lined up.
//
// Sizes are per well and kept in localStorage, so a refresh does not undo the
// arranging someone just did.
// ---------------------------------------------------------------------------

function loadSizes() {
    try {
        return JSON.parse(localStorage.getItem(SIZE_KEY)) || {};
    } catch (err) {
        // Private window, cleared storage, or a browser refusing site data.
        return {};
    }
}

function saveSizes(sizes) {
    try {
        localStorage.setItem(SIZE_KEY, JSON.stringify(sizes));
    } catch (err) {
        // Not worth bothering anyone about - the size just will not persist.
    }
}

const sizes = loadSizes();

function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
}

/* Column width and gap as the grid has actually resolved them. */
function gridMetrics() {
    const style = getComputedStyle(el.grid);

    const tracks = style.gridTemplateColumns
        .split(' ')
        .map(parseFloat)
        .filter((n) => !Number.isNaN(n));

    return {
        columns: Math.max(1, tracks.length),
        track: tracks[0] || 300,
        gap: parseFloat(style.columnGap) || 0,
    };
}

function applySize(card, size) {
    if (size && size.span > 1) {
        // Never wider than the grid currently has room for.
        card.style.gridColumn = 'span ' + clamp(size.span, 1, gridMetrics().columns);
    } else {
        card.style.gridColumn = '';
    }

    // The one property both the alert list and the "all clear" panel read, so
    // a resized card keeps its height whether or not it currently has alerts.
    if (size && size.height) {
        card.style.setProperty('--list-h', size.height + 'px');
    } else {
        card.style.removeProperty('--list-h');
    }
}

function initResize(card, name) {
    for (const grip of card.querySelectorAll('.well-grip')) {

        grip.addEventListener('dblclick', () => {
            delete sizes[name];
            saveSizes(sizes);
            applySize(card, null);
        });

        grip.addEventListener('pointerdown', (event) => {
            event.preventDefault();
            grip.setPointerCapture(event.pointerId);

            const dir = grip.dataset.dir;
            const list = card.querySelector('.alerts');
            const metrics = gridMetrics();

            const startX = event.clientX;
            const startY = event.clientY;
            const startW = card.offsetWidth;

            // A card with nothing wrong hides its list and shows the "all
            // clear" panel instead, so the drag starts from whichever is up.
            const startH =
                list.offsetHeight ||
                card.querySelector('.well-blank').offsetHeight ||
                200;

            const size = { ...(sizes[name] || {}) };

            card.dataset.resizing = dir;
            document.body.dataset.resizing = '1';

            const onMove = (move) => {
                if (dir === 'e' || dir === 'se') {
                    const width = startW + (move.clientX - startX);

                    size.span = clamp(
                        Math.round((width + metrics.gap) / (metrics.track + metrics.gap)),
                        1,
                        Math.min(S.maxSpan, metrics.columns),
                    );
                }

                if (dir === 's' || dir === 'se') {
                    size.height = clamp(
                        startH + (move.clientY - startY),
                        S.minListH,
                        S.maxListH,
                    );
                }

                applySize(card, size);
            };

            const onUp = () => {
                grip.removeEventListener('pointermove', onMove);
                grip.removeEventListener('pointerup', onUp);
                grip.removeEventListener('pointercancel', onUp);

                delete card.dataset.resizing;
                delete document.body.dataset.resizing;

                sizes[name] = size;
                saveSizes(sizes);
            };

            grip.addEventListener('pointermove', onMove);
            grip.addEventListener('pointerup', onUp);
            grip.addEventListener('pointercancel', onUp);
        });
    }
}

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

// function buildCard(well) {
//     const card = el.tplWell.content.firstElementChild.cloneNode(true);

//     const name = card.querySelector('.well-name');
//     const activity = card.querySelector('.well-activity');

//     name.textContent = well.database_name;

//     activity.textContent =
//         well.activity
//             ? `(${well.activity})`
//             : '';

//     // The address is still worth having, just not worth a line of every card.
//     name.title = well.database_name + '  ' + well.ip_address;

//     card.querySelector('.well-edit').addEventListener(
//         'click', () => openModal(well.database_name),
//     );

//     const remove = card.querySelector('.well-remove');

//     // First click arms, second confirms. Avoids a browser dialog for something
//     // that only stops monitoring and can be undone by adding the well back.
//     remove.addEventListener('click', () => {
//         if (remove.dataset.armed) {
//             stopWell(well.database_name);
//             return;
//         }

//         remove.dataset.armed = '1';
//         setTimeout(() => delete remove.dataset.armed, 3000);
//     });

//     initResize(card, well.database_name);
//     applySize(card, sizes[well.database_name]);

//     return card;
// }


function buildCard(well) {
    const card = el.tplWell.content.firstElementChild.cloneNode(true);

    const name = card.querySelector('.well-name');
    const activity = card.querySelector('.well-activity');

    name.textContent = well.database_name;

    activity.textContent = well.activity
        ? `(${well.activity})`
        : '';

    name.title = well.database_name + '  ' + well.ip_address;

    card.querySelector('.well-edit').addEventListener(
        'click', () => openModal(well.database_name),
    );

    const remove = card.querySelector('.well-remove');

    // First click arms it ("Stop?"), a second within three seconds confirms.
    // No browser dialog, and one stray click cannot stop a well.
    remove.addEventListener('click', () => {
        if (remove.dataset.armed) {
            stopWell(well.database_name);
            return;
        }

        remove.dataset.armed = '1';
        setTimeout(() => delete remove.dataset.armed, 3000);
    });

    // The drag handles, and the size this well was last left at.
    initResize(card, well.database_name);
    applySize(card, sizes[well.database_name]);

    return card;
}

function alertRow(alert) {
    const row = el.tplAlert.content.firstElementChild.cloneNode(true);

    row.dataset.key = alert.key;
    row.dataset.tone = alert.tone;
    row.querySelector('.alert-time').textContent = alert.time;
    row.querySelector('.alert-msg').textContent = alert.message;

    return row;
}

/*
  Bring the list in line with `alerts` (newest first) by touching only what
  changed: new alerts are slotted in at the top, ones that have aged out drop
  off the bottom. Rebuilding the whole list instead would replay every row's
  entry animation each time one alert arrived, and jump a card someone is
  scrolled down in.
*/
function renderAlerts(list, alerts) {
    const wanted = new Set(alerts.map((alert) => alert.key));

    for (const row of [...list.children]) {
        if (!wanted.has(row.dataset.key)) {
            row.remove();
        }
    }

    alerts.forEach((alert, position) => {
        const current = list.children[position];

        if (!current || current.dataset.key !== alert.key) {
            list.insertBefore(alertRow(alert), current || null);
        }
    });

    // Every position up to alerts.length now holds the right row, so anything
    // past it is left over from an out-of-order file and can go.
    while (list.children.length > alerts.length) {
        list.lastElementChild.remove();
    }
}



function updateCard(card, state, alerts) {
    const activityNode = card.querySelector('.well-activity');

    activityNode.textContent =
        state.activity
            ? `(${state.activity})`
            : '';
    const list = card.querySelector('.alerts');
    const blank = card.querySelector('.well-blank-text');

    if (alerts.length) {
        card.dataset.tone = worstTone(alerts);
        delete card.dataset.blank;

        card.querySelector('.well-count').textContent = '';

        renderAlerts(list, alerts);

        return;
    }

    // Nothing in the last alertMaxAgeMin minutes. Either the agent has not
    // connected yet, or all is well.
    const starting = Date.now() - state.firstSeen < S.startingMs;

    card.dataset.tone = 'ok';
    card.dataset.blank = '1';
    list.replaceChildren();

    card.querySelector('.well-count').textContent = starting ? 'starting' : 'clear';
    blank.textContent = starting
        ? 'Connecting to the well…'
        : 'Monitoring — nothing out of range';
}

// ---------------------------------------------------------------------------
// The poll
// ---------------------------------------------------------------------------

async function refresh() {
    let wells;

    try {
        wells = (await api('/wells')).wells || {};
        setLink('live', 'Live');
    } catch (err) {
        setLink('down', 'No API');
        return;
    }

    const names = Object.keys(wells).sort();

    // Cards for wells that are no longer monitored.
    for (const [name, state] of cards) {
        if (!(name in wells)) {
            state.card.remove();
            cards.delete(name);
        }
    }

    // Cards for wells that have just appeared.
    for (const name of names) {
        if (!cards.has(name)) {
            const card = buildCard(wells[name]);

            cards.set(name, { card, firstSeen: Date.now() });
            el.grid.append(card);
        }
    }

    // Keep the grid in the same order the names are in, however they arrived.
    names.forEach((name, position) => {
        const card = cards.get(name).card;

        if (el.grid.children[position] !== card) {
            el.grid.insertBefore(card, el.grid.children[position] || null);
        }
    });

    el.empty.toggleAttribute('data-show', names.length === 0);

    // One request per well, all at once - a slow well should not hold up the
    // rest of the board.
    const results = await Promise.all(
        names.map((name) =>
            api(
                '/alerts/' + encodeURIComponent(name)
                + '?limit=' + S.alertLimit
                + '&max_age_minutes=' + S.alertMaxAgeMin,
            )
                .then((body) => listAlerts(body.alerts || []))
                .catch(() => []),
        ),
    );

    names.forEach((name, index) => {
        const state = cards.get(name);

        if (!state) {
            return;
        }

        state.activity = wells[name].activity;

        updateCard(state.card, state, results[index]);
    });

    if (el.statWells) {
        el.statWells.textContent = names.length;
    }

    if (el.statUpdated) {
        el.statUpdated.textContent =
            'updated ' + new Date().toLocaleTimeString();
    }
}

function setLink(state, text) {
    el.linkState.dataset.state = state;
    el.linkText.textContent = text;
}

/*
  One poll at a time.

  setInterval would stack requests up behind a slow or unreachable server and
  then fire them all at once when it came back; chaining the next one off the
  end of the last keeps exactly one in flight.
*/
function startPolling() {
    clearTimeout(polling);

    refresh().finally(() => {
        polling = setTimeout(startPolling, S.pollMs);
    });
}


// ---------------------------------------------------------------------------
// The rule form
//
// Every value the agent checks a well against is entered here, and belongs to
// that well alone: the off-bottom margin that sorts a reading into DRILLING or
// NON DRILLING, then the four blocks in the order the data folder lists them -
// activity, column mapping, conditions, ranges.
//
// `draft` is the single source of truth while the dialog is open - the inputs
// write into it as they are typed, and it is what gets posted. Reading the
// values back out of the DOM at submit time instead would make the shape of
// the rules depend on the shape of the markup.
// ---------------------------------------------------------------------------

let template = null;     // what data/ says a new well should start from
let draft = null;        // the rules being edited right now
let editing = null;      // the well being edited, or null when adding

const copy = (value) => JSON.parse(JSON.stringify(value));

// Only the placeholder in the off-bottom margin box: the value itself comes
// from the template the API serves (rule_files.DEFAULT_DRILLING_CRITERIA), so
// the two cannot drift apart in a way that changes what is saved.
const DEFAULT_DRILLING_CRITERIA = 0.1;

async function getTemplate() {
    if (template === null) {
        template = (await api('/rules/template')).rules;
    }

    return copy(template);
}

/* A labelled input that writes straight into the draft. An empty label leaves
   the caption off and gives just the box - for a field whose section heading
   already says what it is. */
function field(label, value, onInput, opts = {}) {
    const wrap = document.createElement('label');
    wrap.className = 'field' + (opts.compact ? ' field-compact' : '');

    if (label) {
        const name = document.createElement('span');
        name.textContent = label;
        wrap.append(name);
    }

    const input = document.createElement('input');
    input.type = opts.type || 'text';
    input.value = value === undefined || value === null ? '' : value;

    if (opts.placeholder) {
        input.placeholder = opts.placeholder;
    }

    if (opts.title) {
        input.title = opts.title;
    }

    if (opts.type === 'number') {
        input.step = 'any';
    }

    input.addEventListener('input', () => onInput(input.value));

    wrap.append(input);
    return wrap;
}

function section(title) {
    const block = document.createElement('div');
    block.className = 'sub';

    const head = document.createElement('h4');
    head.className = 'sub-head';
    head.textContent = title;

    const body = document.createElement('div');
    body.className = 'sub-body';

    block.append(head, body);
    return { block, body };
}

/* A number, or undefined when the box was left empty. */
function numberOrBlank(text) {
    const trimmed = String(text).trim();

    if (trimmed === '') {
        return undefined;
    }

    const value = Number(trimmed);

    return Number.isNaN(value) ? trimmed : value;
}

// ---- activity: one tickbox per parameter, per activity --------------------

function renderActivity() {
    const host = document.getElementById('fields-activity');
    host.replaceChildren();

    for (const [activity, rules] of Object.entries(draft.activity)) {
        const { block, body } = section(activity);
        body.className = 'sub-body sub-body-flags';

        for (const param of Object.keys(rules)) {
            const wrap = document.createElement('label');
            wrap.className = 'flag';

            const box = document.createElement('input');
            box.type = 'checkbox';
            box.checked = rules[param] === 1;

            // 1 and 0 in the file, not true/false - the agent tests for 1.
            box.addEventListener('change', () => {
                draft.activity[activity][param] = box.checked ? 1 : 0;
            });

            const name = document.createElement('span');
            name.textContent = param;

            wrap.append(box, name);
            body.append(wrap);
        }

        host.append(block);
    }
}

/*
  The one number that decides which set of activity rules a reading is checked
  against: hole depth minus bit depth at or under it is DRILLING, over it is
  NON DRILLING. It is this well's own, so a rig whose depth channels sit half
  a metre apart on bottom can say so without moving every other well with it.

  Just the box: the section heading above it already says what the number is
  and what unit it is in, and a caption would only repeat it.

  numberOrBlank, like every other figure in the form: an empty box stays empty
  and a word stays a word, and the API answers with the sentence saying what
  is wrong with it. Reading it as `Number(value) || 0` instead would quietly
  turn both into 0 - a margin of nothing, which calls the rig NON DRILLING
  from the moment the bit lifts by a millimetre.
*/
function renderDrillingCriteria() {
    const host = document.getElementById('fields-drilling-criteria');
    host.replaceChildren();

    // No min="0" on the box, though a negative margin is refused: a number
    // input the browser judges invalid blocks submit with a bubble it cannot
    // show while its section is collapsed, and the form would just stop
    // responding. Every other figure here is checked by the API and answered
    // in #modal-error, and this one is checked the same way.
    host.append(field(
        '',
        draft.drilling_criteria,
        (value) => {
            draft.drilling_criteria = numberOrBlank(value);
        },
        {
            compact: true,
            type: 'number',
            placeholder: String(DEFAULT_DRILLING_CRITERIA),
            title: 'Hole depth − bit depth at or under this is DRILLING, '
                + 'over it is NON DRILLING',
        },
    ));
}

/*
  The three list-shaped blocks are laid out as tables.

  Each row used to carry its own labels ("SECONDS", "% CHANGE") above inputs
  that stretched to fill the row, so a block with one value had a box the
  width of the dialog and the labels repeated down the page. Naming the
  columns once at the top and keeping the inputs at the width of the numbers
  they hold makes the values line up and the section a third of the height.
*/

function table(columns) {
    const grid = document.createElement('div');
    grid.className = 'table';
    grid.style.setProperty('--cols', columns.map((c) => c.width).join(' '));

    const head = document.createElement('div');
    head.className = 'thead';

    for (const column of columns) {
        const cell = document.createElement('span');
        cell.textContent = column.label;
        head.append(cell);
    }

    grid.append(head);
    return grid;
}

function row(grid, name) {
    const line = document.createElement('div');
    line.className = 'trow';

    const label = document.createElement('span');
    label.className = 'tname';
    label.textContent = name;
    label.title = name;

    line.append(label);
    grid.append(line);
    return line;
}

/* A bare input for a table cell - the column header is its label. */
function cell(value, onInput, opts = {}) {
    const input = document.createElement('input');

    input.type = opts.type || 'text';
    input.value = value === undefined || value === null ? '' : value;
    input.title = opts.title || '';

    if (opts.type === 'number') {
        input.step = 'any';
    }

    if (opts.placeholder) {
        input.placeholder = opts.placeholder;
    }

    input.addEventListener('input', () => onInput(input.value));
    return input;
}

function blank(line) {
    line.append(document.createElement('span'));
}

// ---- column mapping ------------------------------------------------------

function renderMapping() {
    const host = document.getElementById('fields-column_mapping');
    host.replaceChildren();

    const grid = table([
        { label: 'Parameter', width: '132px' },
        { label: 'Columns on this rig', width: 'minmax(200px, 1fr)' },
    ]);

    for (const logical of Object.keys(draft.column_mapping)) {
        const line = row(grid, logical);

        line.append(cell(
            draft.column_mapping[logical].join(', '),
            (text) => {
                draft.column_mapping[logical] = text
                    .split(',')
                    .map((name) => name.trim())
                    .filter(Boolean);
            },
            { placeholder: 'column name, another name', title: 'Comma separated' },
        ));
    }

    host.append(wrapScroll(grid));
}

// ---- conditions ----------------------------------------------------------

// These two carry a duration and no percentage; the API refuses one anyway.
const DURATION_ONLY = new Set(['TA_TG', 'HOOKLOAD']);

function renderConditions() {
    const host = document.getElementById('fields-conditions');
    host.replaceChildren();

    const grid = table([
        { label: 'Check', width: '132px' },
        { label: 'Seconds', width: '112px' },
        { label: '% change', width: '112px' },
    ]);

    for (const name of Object.keys(draft.conditions)) {
        const entry = draft.conditions[name];
        const line = row(grid, name);

        line.append(cell(entry.duration_seconds, (v) => {
            entry.duration_seconds = numberOrBlank(v);
        }, { type: 'number', title: 'How long before it counts' }));

        if (DURATION_ONLY.has(name)) {
            blank(line);
        } else {
            line.append(cell(entry.percentage_change, (v) => {
                entry.percentage_change = numberOrBlank(v);
            }, { type: 'number', title: 'How far it has to move' }));
        }
    }

    host.append(wrapScroll(grid));
}

// ---- ranges --------------------------------------------------------------

/* What the limits for a parameter are written in, for the tooltips. */
function unitOf(param) {
    return (draft.ranges[param] || {}).unit || 'the limit unit';
}

function renderRanges() {
    const host = document.getElementById('fields-ranges');
    host.replaceChildren();

    const grid = table([
        { label: 'Parameter', width: '132px' },
        { label: 'Min', width: '96px' },
        { label: 'Max', width: '96px' },
        { label: 'Unit', width: '86px' },
        { label: 'Factor', width: '86px' },
    ]);

    for (const param of Object.keys(draft.ranges)) {
        const limits = draft.ranges[param];
        const line = row(grid, param);

        line.append(cell(limits.min, (v) => {
            limits.min = numberOrBlank(v);
        }, { type: 'number' }));

        line.append(cell(limits.max, (v) => {
            limits.max = numberOrBlank(v);
        }, { type: 'number' }));

        line.append(cell(limits.unit, (v) => {
            const text = v.trim();

            if (text) {
                limits.unit = text;
            } else {
                delete limits.unit;
            }
        }, { title: 'Shown in the alert text' }));

        // Converts the stored reading before it is compared, so the limits can
        // be written in the unit you think in. It multiplies, except for ROP,
        // where the column is minutes per metre and the limits are m/hr, so it
        // divides: 60 / 0.5 = 120 m/hr. Blank compares as stored.
        line.append(cell(limits.factor, (v) => {
            const value = numberOrBlank(v);

            if (value === undefined) {
                delete limits.factor;
            } else {
                limits.factor = value;
            }
        }, {
            type: 'number',
            title: param.toUpperCase() === 'ROP'
                ? 'Divides: the column is minutes per metre, the limits are '
                    + unitOf(param) + ' - 60 / 0.5 = 120'
                : 'Multiplies the reading before comparing',
        }));
    }

    host.append(wrapScroll(grid));
}

/* Tables keep their columns on a narrow screen and scroll sideways instead of
   collapsing into a stack where the headers no longer line up. */
function wrapScroll(grid) {
    const scroller = document.createElement('div');
    scroller.className = 'table-scroll';
    scroller.append(grid);
    return scroller;
}

function renderRules() {
    renderDrillingCriteria();
    renderActivity();
    renderMapping();
    renderConditions();
    renderRanges();
}

// ---- display names -------------------------------------------------------
//
// Not part of any well: one file, data/display_name.json, shared by every well
// and edited on its own tab in Settings with its own save. A name typed here is
// what alerts call the parameter - "Weight on bit" rather than "WOB" - from the
// next reading after it is saved.
//
// The file decides which parameters are listed: exactly the ones in it, in its
// order. One added to the file by hand is here the next time Settings opens.

let displayNames = null;   // parameter -> name, as being edited

function renderDisplayNames() {
    const host = document.getElementById('fields-display_name');
    host.replaceChildren();

    const params = Object.keys(displayNames);

    if (!params.length) {
        const note = document.createElement('p');
        note.className = 'modal-intro';
        note.textContent = 'display_name.json has no parameters yet. Add one there, '
            + 'e.g. "WOB": "Weight on bit", and it appears here.';
        host.append(note);
        return;
    }

    const grid = table([
        { label: 'Parameter', width: '132px' },
        { label: 'Shown in alerts as', width: 'minmax(200px, 1fr)' },
    ]);

    for (const param of params) {
        const line = row(grid, param);

        // Kept even when blank: the list comes from the file, so a name that
        // was deleted on save would take its parameter off this tab for good.
        line.append(cell(displayNames[param], (text) => {
            displayNames[param] = text.trim();
        }, { placeholder: param, title: 'Shown in alerts instead of ' + param }));
    }

    host.append(wrapScroll(grid));
}

function showNamesError(message) {
    el.displayNamesError.textContent = message || '';
    el.displayNamesError.hidden = !message;
}

// A form of its own, so Enter in any box saves the names too.
el.displayNamesForm.addEventListener('submit', async (event) => {
    event.preventDefault();

    if (displayNames === null) {
        return;
    }

    showNamesError('');

    const blank = Object.keys(displayNames).filter((param) => !displayNames[param]);

    if (blank.length) {
        showNamesError(
            blank.join(', ') + (blank.length === 1 ? ' needs' : ' need') + ' a name. '
            + 'To show a parameter as it is, type its own name (e.g. ' + blank[0] + ').',
        );
        return;
    }

    el.displayNamesSave.disabled = true;

    try {
        await api('/rules/display_name', {
            method: 'PUT',
            body: JSON.stringify(displayNames),
        });

        toast('Display names saved — new alerts use them within a second');
        closeModal();
    } catch (err) {
        showNamesError(err.message);
    } finally {
        el.displayNamesSave.disabled = false;
    }
});

/* Settings has two tabs: adding a well, and the display names. */
function showTab(tab) {
    for (const button of el.modalTabs.querySelectorAll('.modal-tab')) {
        button.setAttribute('aria-selected', String(button.dataset.tab === tab));
    }

    el.form.hidden = tab !== 'well';
    el.displayNamesForm.hidden = tab !== 'names';
}

for (const button of el.modalTabs.querySelectorAll('.modal-tab')) {
    button.addEventListener('click', () => showTab(button.dataset.tab));
}


// ---------------------------------------------------------------------------
// Adding, editing and removing wells
// ---------------------------------------------------------------------------

function showError(message) {
    el.modalError.textContent = message || '';
    el.modalError.hidden = !message;
}

/*
  Open the dialog.

  With no name it is Settings: a new well, on the template from data/ so only
  the handful of values that differ for this rig have to be touched, and the
  display names on their own tab. With a name it is that well's own saved
  rules and nothing else, and saving replaces them.
*/
async function openModal(name) {
    editing = name || null;
    showError('');
    showNamesError('');

    // Display names apply to every well, so they are only offered in Settings.
    // Under one well's pencil they would look like that well's own.
    el.modalTabs.hidden = Boolean(editing);
    showTab('well');

    el.modalTitle.textContent = editing ? 'Edit ' + editing : 'Settings';
    el.submit.textContent = editing ? 'Save rules' : 'Start monitoring';
    // Editing saves the rules only (PUT /wells/{name}/rules keeps the address),
    // so neither box can be changed - an edited IP would be silently dropped.
    el.dbName.disabled = Boolean(editing);
    el.ipAddress.disabled = Boolean(editing);

    el.modal.hidden = false;

    try {
        if (editing) {
            const record = await api('/wells/' + encodeURIComponent(editing));

            el.dbName.value = record.database_name;
            el.ipAddress.value = record.ip_address;
            draft = record.rules;
        } else {
            el.dbName.value = '';
            el.ipAddress.value = '';
            draft = await getTemplate();
        }

        renderRules();

        if (!editing) {
            el.dbName.focus();
        }
    } catch (err) {
        showError('Could not load the rules: ' + err.message);
    }

    if (editing) {
        return;
    }

    // Separately, so a problem with the names does not stop a well being added.
    try {
        displayNames = await api('/rules/display_name');
        renderDisplayNames();
    } catch (err) {
        showNamesError('Could not load the display names: ' + err.message);
    }
}

function closeModal() {
    el.modal.hidden = true;
    draft = null;
    editing = null;
    displayNames = null;

    // Not left for the next opening to show while the names are fetched again.
    document.getElementById('fields-display_name').replaceChildren();
}

async function stopWell(name) {
    try {
        await api('/wells/' + encodeURIComponent(name), { method: 'DELETE' });
        toast(name + ' is no longer monitored');
    } catch (err) {
        toast(err.message, 'error');
    }

    loadSettings().then(startPolling);
}

el.form.addEventListener('submit', async (event) => {
    event.preventDefault();
    showError('');

    const database_name = el.dbName.value.trim();
    const ip_address = el.ipAddress.value.trim();

    if (!database_name || !ip_address) {
        showError('A database name and an IP address are both needed.');
        return;
    }

    // POST /wells with a name already monitored replaces that well's rules
    // with this form - which opened on the template, not on its own rules.
    // Changing an existing well is what its pencil is for.
    if (!editing && cards.has(database_name)) {
        showError(
            database_name + ' is already being monitored. Adding it again would replace '
            + 'its rules with the defaults in this form - to change them, use the '
            + 'pencil on its card.',
        );
        return;
    }

    el.submit.disabled = true;
    el.submit.textContent = 'Saving…';

    try {
        if (editing) {
            await api('/wells/' + encodeURIComponent(editing) + '/rules', {
                method: 'PUT',
                body: JSON.stringify(draft),
            });

            toast(editing + ' updated — its agent reloads within a second');
        } else {
            await api('/wells', {
                method: 'POST',
                body: JSON.stringify({ database_name, ip_address, rules: draft }),
            });

            toast(database_name + ' added — the agent starts within a few seconds');
        }

        closeModal();
        startPolling();
    } catch (err) {
        // The API says which block and which parameter is wrong, so it is
        // shown against the form rather than in a toast that disappears.
        showError(err.message);
    } finally {
        el.submit.disabled = false;
        el.submit.textContent = editing ? 'Save rules' : 'Start monitoring';
    }
});

// Back to what data/ says, for when a well has been edited into a corner.
el.resetDefaults.addEventListener('click', async () => {
    try {
        draft = await getTemplate();
        renderRules();
        showError('');
        toast('Rules reset to the defaults in data/ — not saved yet');
    } catch (err) {
        showError(err.message);
    }
});

// ---------------------------------------------------------------------------
// Opening and closing
// ---------------------------------------------------------------------------

document.getElementById('settings-open').addEventListener('click', () => openModal());
document.getElementById('settings-close').addEventListener('click', closeModal);

for (const button of document.querySelectorAll('[data-open-settings]')) {
    button.addEventListener('click', () => openModal());
}

el.modal.addEventListener('click', (event) => {
    if (event.target === el.modal) {
        closeModal();
    }
});

document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !el.modal.hidden) {
        closeModal();
    }
});

// ---------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------

function toast(message, tone) {
    const node = document.createElement('div');

    node.className = 'toast';
    node.textContent = message;

    if (tone) {
        node.dataset.tone = tone;
    }

    el.toasts.append(node);

    setTimeout(() => {
        node.dataset.leaving = '1';
        setTimeout(() => node.remove(), 200);
    }, 3600);
}

// ---------------------------------------------------------------------------

// A card widened on a big screen would hang off a smaller one, so spans are
// re-clamped against the columns the grid actually has now.
window.addEventListener('resize', () => {
    for (const [name, state] of cards) {
        applySize(state.card, sizes[name]);
    }
});

// Stop polling while the tab is hidden; pick straight back up on return.
document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        clearTimeout(polling);
    } else {
        startPolling();
    }
});

startPolling();
