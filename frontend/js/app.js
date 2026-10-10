/*
  DataQC dashboard.

  Served by config_api.py, normally from the same origin as the endpoints it
  calls - see API_BASE in shared.js for the case where it is not.

  It talks to these:
      GET    /rules/template            what a new well's form is filled with
      GET    /wells                     which wells are monitored
      POST   /wells                     start one, with its own rules
      GET    /wells/{name}              one well, rules included
      PUT    /wells/{name}/rules        change that well's rules
      DELETE /wells/{name}              stop monitoring one
      GET    /alerts/{name}             what that well's agent has raised
      GET    /alerts/all/{name}         every alert that well has ever raised
      GET    /rules/display_name        what each parameter is called in alerts
      PUT    /rules/display_name        change those names, for every well

  /wells should also say whether each well's data is still arriving:
      "feed": "live" | "stale" | "down"
      "feed_stale_seconds": seconds since the last new row
  Without it the card cannot tell a quiet well from a stopped one, because a
  stopped feed raises no alerts and "no alerts" looks exactly like "all clear".

  Rules belong to one well: what is entered for a well affects that well only.

  Cards are built once per well and updated in place afterwards. Re-rendering
  the grid every poll would restart every animation and throw away the user's
  scroll position in a card they were reading.
*/

'use strict';

/*
  Where the API is and how to read one alert - API_BASE, api(), parseAlert(),
  classify(), escapeHtml() - live in shared.js, which index.html loads before
  this file. logs.html loads the same one, so the two pages cannot drift into
  disagreeing about what an alert says or which port to ask. Everything below
  is the dashboard's alone.
*/

/*
  Every timing and limit below is set in .env and served by GET /settings, so
  this file is not where any of them is changed. What is written here is only
  the fallback used until that call answers, and if it never does - the page
  still works against a server too old to have the endpoint, or one that is
  briefly down, rather than coming up blank.

  They are read through the `S` object rather than as bare constants so that
  the values a poll uses are the ones in force now, not the ones that happened
  to be compiled in.
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

    // Seconds without a new row before a well is called stopped. Used only
    // when the server sends an age but no explicit "feed" status.
    staleFeedSec: 120,
};


const auth = { user: null };
const can = (permission) => Boolean(auth.user && auth.user.permissions.includes(permission));


async function loadUser() {
    auth.user = await api('/auth/me');

    renderUserMenu(auth.user, {
        canAddPerson: can('add_user'),
        logoutHref: API_BASE + '/auth/logout',
    });

    const anySettings = ['add_well', 'display_names', 'email_settings'].some(can);

    document.body.toggleAttribute('data-no-settings', !anySettings);
    document.body.toggleAttribute('data-no-edit', !can('edit_rules'));
}



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

        if (s.stale_feed_seconds) {
            S.staleFeedSec = s.stale_feed_seconds;
        }
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
    linkState: document.getElementById('link-state'),
    linkText: document.getElementById('link-text'),
    modal: document.getElementById('modal'),
    form: document.getElementById('add-well-form'),
    dbName: document.getElementById('database_name'),
    ipAddress: document.getElementById('ip_address'),
    region: document.getElementById('region'),
    regionField: document.getElementById('region-field'),
    submit: document.getElementById('add-submit'),
    modalTitle: document.getElementById('modal-title'),
    modalError: document.getElementById('modal-error'),
    resetDefaults: document.getElementById('reset-defaults'),
    showLogs: document.getElementById('show-logs'),
    toasts: document.getElementById('toasts'),
    tplWell: document.getElementById('tpl-well'),
    tplAlert: document.getElementById('tpl-alert'),
    modalTabs: document.getElementById('modal-tabs'),
    displayNamesForm: document.getElementById('display-names-form'),
    displayNamesSave: document.getElementById('display-names-save'),
    displayNamesError: document.getElementById('display-names-error'),
};

// The form's sections behave as an accordion: opening one closes whichever
// else was open, instead of letting them all stack open together.
function initAccordion(container) {
    const groups = [...container.querySelectorAll('details.group')];

    for (const details of groups) {
        details.addEventListener('toggle', () => {
            if (!details.open) {
                return;
            }

            for (const other of groups) {
                if (other !== details) {
                    other.open = false;
                }
            }
        });
    }
}

initAccordion(el.form);

// database_name -> { card, firstSeen, activity, feed }
const cards = new Map();

let polling = null;

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------

/*
  An alert that says "... from 120s" is one ongoing condition, not a new
  event each time. Series key = "CO2|above". Later readings replace earlier
  ones, and the row moves to the position of the latest reading.
*/
// "Co2 : 72.44% above limit 0.5% from 107s"
const SERIES_RE = /^(.*?) : [-\d.]+\S*\s+(above|below) limit .*? from \d+s\b/;

// "SPP out of range from 25s"
const SPP_RE = /^(.*?) out of range from \d+s\b/;

function seriesKey(message) {
    const m = SERIES_RE.exec(message);
    if (m) return m[1] + '|' + m[2];

    const s = SPP_RE.exec(message);
    if (s) return s[1] + '|range';

    return null;
}

function listAlerts(raw) {
    const seen = new Map();
    const items = new Map();   // insertion order = display order (oldest first)

    for (const entry of raw) {
        const text = String(entry);
        const { time, message } = parseAlert(text);
        const series = seriesKey(message);

        let key;
        if (series) {
            key = 'series:' + series;
        } else {
            const n = (seen.get(text) || 0) + 1;
            seen.set(text, n);
            key = text + '#' + n;
        }

        items.delete(key);   // re-insert so the latest reading sorts last
        items.set(key, { key, time, message, ...classify(message) });
    }

    return [...items.values()].reverse();   // newest first
}

/*
  Bring the list in line with `alerts` (newest first) by touching only what
  changed. Rebuilding the whole list instead would replay every row's entry
  animation each time one alert arrived, and jump a card someone is scrolled
  down in.
*/
function renderAlerts(list, alerts) {
    const existing = new Map([...list.children].map((row) => [row.dataset.key, row]));
    const wanted = new Set(alerts.map((a) => a.key));

    for (const [key, row] of existing) {
        if (!wanted.has(key)) {
            row.remove();
            existing.delete(key);
        }
    }

    alerts.forEach((alert, position) => {
        let row = existing.get(alert.key);

        if (!row) {
            row = alertRow(alert);            // new: gets the entry animation
        } else {
            // Same alert, newer reading: update text in place, no re-animation.
            if (row.dataset.sig !== alert.time + alert.message) {
                row.querySelector('.alert-time').textContent = alert.time;
                row.querySelector('.alert-msg').textContent = alert.message;
            }
            row.dataset.tone = alert.tone;
        }

        row.dataset.sig = alert.time + alert.message;

        if (list.children[position] !== row) {
            list.insertBefore(row, list.children[position] || null);
        }
    });
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

/*
  What the rig is doing, next to the well's name.

  One function rather than the same two lines in several places: the card is
  built once and then updated in place, and a poll that could not fetch a
  well's alerts still knows its activity - so every path has to write this the
  same way or the card ends up saying nothing.

  Blank while it is null, which is what /wells sends when the rig cannot be
  reached. A stale "(DRILLING)" there would say the bit is on bottom when in
  fact nobody can see it. The same goes for a stopped feed: callers pass null.
*/
function showActivity(card, activity) {
    card.querySelector('.well-activity').textContent =
        activity ? `(${activity})` : '';
}

/*
  Is this well's data still arriving?

  Reads what /wells says: "feed" ("live" | "stale" | "down") and
  "feed_stale_seconds". If the server sends only an age
  ("last_row_age_seconds" or "feed_stale_seconds") and no "feed", the age is
  judged against S.staleFeedSec here.

  Without any of these the well is assumed live, which is the old behaviour -
  so a server that has not been updated yet still works.
*/
function feedStatus(well) {
    const secs = well.feed_stale_seconds ?? well.last_row_age_seconds ?? null;

    if (well.feed === 'down') {
        return { bad: true, kind: 'down', secs };
    }

    if (well.feed === 'stale') {
        return { bad: true, kind: 'stale', secs };
    }

    if (!well.feed && secs !== null && secs > S.staleFeedSec) {
        return { bad: true, kind: 'stale', secs };
    }

    return { bad: false, kind: 'live', secs };
}

function formatAge(secs) {
    if (!secs) {
        return '';
    }

    if (secs < 60) {
        return secs + 's';
    }

    const mins = Math.floor(secs / 60);

    if (mins < 60) {
        return mins + ' min';
    }

    return Math.floor(mins / 60) + 'h ' + (mins % 60) + 'm';
}

function feedText(feed) {
    if (feed.kind === 'down') {
        return 'Well stopped — no data is coming in';
    }

    const age = formatAge(feed.secs);

    return 'Well stopped — no new data' + (age ? ' for ' + age : '')
        + '. Nothing is being checked.';
}

function feedLabel(feed) {
    return feed.kind === 'down' ? 'no data' : 'well stoped';
}


function buildCard(well) {
    const card = el.tplWell.content.firstElementChild.cloneNode(true);

    const name = card.querySelector('.well-name');

    name.textContent = well.database_name;

    const feed = feedStatus(well);

    showActivity(card, feed.bad ? null : well.activity);
    showDepths(card, well);

    name.title = well.database_name + '  ' + well.ip_address;

    card.querySelector('.well-edit').addEventListener(
        'click', () => openModal(well.database_name),
    );

    // Logs button (needs the .well-logs button in #tpl-well)
    const logsBtn = card.querySelector('.well-logs');

    if (logsBtn) {
        logsBtn.hidden = !can('view_logs');
        logsBtn.addEventListener('click', () => openLogs(well.database_name));
    }

    // Hide the stop button for roles without stop_well
    const remove = card.querySelector('.well-remove');
    // remove.hidden = !can('stop_well');

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

    // The pin button, and whether this well was left pinned.
    initPin(card, well.database_name);

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

function updateCard(card, state, alerts) {
    const feed = state.feed || { bad: false, kind: 'live', secs: null };

    // A stopped feed has no current activity, so do not claim one.
    showActivity(card, feed.bad ? null : state.activity);

    const list = card.querySelector('.alerts');
    const blank = card.querySelector('.well-blank-text');
    const count = card.querySelector('.well-count');

    card.toggleAttribute('data-feed-bad', feed.bad);

    if (alerts.length) {
        card.dataset.tone = feed.bad ? 'critical' : worstTone(alerts);
        delete card.dataset.blank;

        count.textContent = feed.bad ? feedLabel(feed) : '';

        renderAlerts(list, alerts);

        return;
    }

    list.replaceChildren();
    card.dataset.blank = '1';

    // No alerts is only "all clear" when the data is actually arriving.
    if (feed.bad) {
        card.dataset.tone = 'critical';
        count.textContent = feedLabel(feed);
        blank.textContent = feedText(feed);
        return;
    }

    // Nothing in the last alertMaxAgeMin minutes. Either the agent has not
    // connected yet, or all is well.
    const starting = Date.now() - state.firstSeen < S.startingMs;

    card.dataset.tone = 'ok';
    count.textContent = starting ? 'starting' : '';
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
                .catch(() => null),
        ),
    );

    names.forEach((name, index) => {
        const state = cards.get(name);

        if (!state) {
            return;
        }

        // Activity and feed both come from /wells, not from the alerts call,
        // so they are known even on a pass where a well's alerts could not be
        // read. They have to be copied onto the card's state or updateCard()
        // reads undefined and blanks them.
        state.activity = wells[name].activity;
        state.feed = feedStatus(wells[name]);

        showDepths(state.card, wells[name]);

        if (results[index] === null) {
            // Alerts could not be fetched this pass. Keep the rows already
            // drawn, but the activity and feed did arrive and must not go
            // stale.
            showActivity(state.card, state.feed.bad ? null : state.activity);
            state.card.toggleAttribute('data-feed-bad', state.feed.bad);
            return;
        }

        updateCard(state.card, state, results[index]);
    });

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
const DEFAULT_DRILLING_CRITERIA = 0.05;

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
  The numbers that decide which set of activity rules a reading is checked
  against: hole depth minus bit depth at or under the off-bottom margin is
  DRILLING, over it is NON DRILLING. They are this well's own, so a rig whose
  depth channels sit half a metre apart on bottom can say so without moving
  every other well with it.

  numberOrBlank, like every other figure in the form: an empty box stays empty
  and a word stays a word, and the API answers with the sentence saying what
  is wrong with it. Reading it as `Number(value) || 0` instead would quietly
  turn both into 0 - a margin of nothing, which calls the rig NON DRILLING
  from the moment the bit lifts by a millimetre.
*/
function renderDrillingCriteria() {
    const host = document.getElementById('criteria-row');

    host.replaceChildren();

    host.append(
        field(
            'Off-bottom margin',
            draft.drilling_criteria,
            (value) => {
                draft.drilling_criteria = numberOrBlank(value);
            },
            { placeholder: '0.05' },
        )
    );

    host.append(
        field(
            'Bit Depth Threshold Drilling',
            draft.bd_threshold_drilling,
            (value) => {
                draft.bd_threshold_drilling = numberOrBlank(value);
            },
            { placeholder: '10' },
        )
    );

    host.append(
        field(
            'Bit Depth Threshold Non Drilling',
            draft.bd_threshold_non_drilling,
            (value) => {
                draft.bd_threshold_non_drilling = numberOrBlank(value);
            },
            { placeholder: '100' },
        )
    );
}

/*
  The three list-shaped blocks are laid out as tables.

  Naming the columns once at the top and keeping the inputs at the width of
  the numbers they hold makes the values line up and the section a third of
  the height it was when every row carried its own labels.
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

// These carry a duration and no percentage; the API refuses one anyway.
const DURATION_ONLY = new Set(['TA_TG']);

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

    const blankNames = Object.keys(displayNames).filter((param) => !displayNames[param]);

    if (blankNames.length) {
        showNamesError(
            blankNames.join(', ') + (blankNames.length === 1 ? ' needs' : ' need') + ' a name. '
            + 'To show a parameter as it is, type its own name (e.g. ' + blankNames[0] + ').',
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

/* Settings has three tabs: adding a well, the display names, and email. */
function showTab(tab) {
    for (const button of el.modalTabs.querySelectorAll('.modal-tab')) {
        button.setAttribute('aria-selected', String(button.dataset.tab === tab));
    }

    el.form.hidden = tab !== 'well';
    el.displayNamesForm.hidden = tab !== 'names';
    emailEl.form.hidden = tab !== 'email';

    if (tab === 'well' && !editing) {
        requestAnimationFrame(() => el.dbName.focus());
    }
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

    el.showLogs.hidden = !editing;

    showError('');
    showNamesError('');

    // Display names apply to every well, so they are only offered in Settings.
    // Under one well's pencil they would look like that well's own.
    el.modalTabs.hidden = Boolean(editing);
    showTab('well');

    el.modalTitle.textContent = editing ? 'Edit ' + editing : 'Settings';
    el.submit.textContent = editing ? 'Save rules' : 'Start monitoring';

    // The name is what the well's file is called and what its agent is keyed
    // on, so it cannot change. The address can: PUT /wells/{name} saves it
    // beside the rules, so an edited IP is kept rather than silently dropped.
    el.dbName.disabled = Boolean(editing);
    el.ipAddress.disabled = Boolean(editing);

    // The base region is on the form both when a well is added and under its
    // pencil, so a well added without one - or moved to another base - can be
    // given it later. It decides who gets emailed and is shown in the report.
    el.regionField.hidden = false;

    el.modal.hidden = false;

    try {
        if (editing) {
            const record = await api('/wells/' + encodeURIComponent(editing));

            el.dbName.value = record.database_name;
            el.ipAddress.value = record.ip_address;
            el.region.value = record.region || '';
            draft = record.rules;
        } else {
            el.dbName.value = '';
            el.ipAddress.value = '';
            el.region.value = '';
            draft = await getTemplate();
        }

        renderRules();

        if (!editing) {
            requestAnimationFrame(() => el.dbName.focus());
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

    // Also separately, and also only in Settings. Loading it here rather than
    // when its tab is clicked is what fills the region datalist before the
    // well form is typed into, which is the whole point of the datalist.
    loadEmailRecipients();
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

    // Optional: a well with no region is monitored exactly as before and
    // simply appears in no digest.
    const region = el.region.value.trim();

    if (!database_name || !ip_address) {
        showError('A database name and an IP address are both needed.');
        return;
    }

    // POST /wells with a name already monitored replaces that well's rules
    // with this form - which opened on the template, not on its own rules.
    // Changing an existing well is what its pencil is for. Case is ignored:
    // "DK-1123" and "dk-1123" are the same rig. The server refuses it too.
    const existing = [...cards.keys()].find(
        (name) => name.trim().toLowerCase() === database_name.toLowerCase(),
    );

    if (!editing && existing) {
        showError(
            existing + ' is already being monitored. Adding it again would replace '
            + 'its rules with the defaults in this form - to change them, use the '
            + 'pencil on its card.',
        );
        return;
    }

    el.submit.disabled = true;
    el.submit.textContent = 'Saving…';

    try {
        if (editing) {
            // Two calls, because they are two different things: the address
            // and base region are the well's details, the thresholds are its
            // rules. The region box opened on the stored region, so sending
            // it back unchanged keeps the base the rig is already on.
            await api('/wells/' + encodeURIComponent(editing), {
                method: 'PUT',
                body: JSON.stringify({ ip_address, region }),
            });

            await api('/wells/' + encodeURIComponent(editing) + '/rules', {
                method: 'PUT',
                body: JSON.stringify(draft),
            });

            toast(editing + ' updated — its agent reloads within a second');
        } else {
            await api('/wells', {
                method: 'POST',
                body: JSON.stringify({ database_name, ip_address, region, rules: draft }),
            });

            toast(database_name + ' added — the agent starts within a few seconds');
        }

        closeModal();
        loadUser().then(startPolling);
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
// Show Logs - every alert a well has ever raised, in its own tab
//
// The tab is logs.html, and it is a page in its own right: it reads the well
// out of its own query string, fetches /alerts/all itself and polls for more
// (see logs.js). All this does is open it.
//
// The window is opened synchronously, inside the click itself - opening it
// after an `await` resolves puts it outside the gesture that triggered the
// click, and browsers treat that as an unrequested popup and block it.
// ---------------------------------------------------------------------------

el.showLogs.addEventListener('click', () => {
    if (!editing) {
        return;
    }

    // Relative, not "/logs.html": the page is then found next to this one
    // wherever it is served from - the API's own root, a static server's
    // sub-path, or a file:// directory.
    //
    // ?api= is carried over when this page was opened with it, so the log tab
    // asks the same API rather than its own origin. Normally there is none and
    // both pages just use a relative path - see API_BASE in shared.js.
    let url = 'logs.html?well=' + encodeURIComponent(editing);

    if (API_PARAM) {
        url += '&api=' + encodeURIComponent(API_PARAM);
    }

    // Named after the well rather than '_blank', so clicking Show Logs twice
    // for the same well focuses the tab already open on it instead of stacking
    // up a new one, while two different wells still get a tab each.
    const tab = window.open(url, 'dataqc-logs-' + editing);

    if (!tab) {
        toast('Your browser blocked the popup - allow popups for this site and try again.', 'error');
        return;
    }

    tab.focus();
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
        loadUser().then(startPolling);
    }
});

loadUser().then(startPolling);



