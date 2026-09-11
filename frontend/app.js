/*
  DataQC dashboard.

  Served by config_api.py, normally from the same origin as the endpoints it
  calls - see API_BASE below for the case where it is not.

  It talks to four things:
      GET    /wells                  which wells are monitored
      POST   /wells                  start monitoring one
      DELETE /wells/{database_name}  stop monitoring one
      GET    /alerts/{name}          what that well's agent has raised

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

const POLL_MS = 2500;

// How long after a well appears to keep saying "starting" rather than
// "all clear" - the manager takes up to five seconds to notice a new well.
const STARTING_MS = 8000;

// How many alerts to pull per well. They repeat once a second while a problem
// stands, so this is a window of recent history, not the whole file.
const ALERT_LIMIT = 400;

// Resize limits. Height is in pixels of alert list; width is in grid columns.
const MIN_LIST_H = 90;
const MAX_LIST_H = 900;
const MAX_SPAN = 4;

// Where per-card sizes are remembered between visits.
const SIZE_KEY = 'dataqc.card-sizes';

// ---------------------------------------------------------------------------
// Elements
// ---------------------------------------------------------------------------

const el = {
    grid: document.getElementById('grid'),
    empty: document.getElementById('empty'),
    statWells: document.getElementById('stat-wells'),
    statAlerts: document.getElementById('stat-alerts'),
    statUpdated: document.getElementById('stat-updated'),
    linkState: document.getElementById('link-state'),
    linkText: document.getElementById('link-text'),
    modal: document.getElementById('modal'),
    form: document.getElementById('add-well-form'),
    dbName: document.getElementById('database_name'),
    ipAddress: document.getElementById('ip_address'),
    submit: document.getElementById('add-submit'),
    toasts: document.getElementById('toasts'),
    tplWell: document.getElementById('tpl-well'),
    tplAlert: document.getElementById('tpl-alert'),
};

// database_name -> { card, firstSeen, signature }
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
    [/is above maximum limit/i, 'critical'],
    [/TA is greater than TG/i, 'critical'],
    [/Cannot determine activity/i, 'critical'],
    [/Unknown activity/i, 'critical'],
    [/is below minimum limit/i, 'warn'],
    [/cannot be 0/i, 'warn'],
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
  Collapse the repeats.

  A problem that stands for a minute is written to the file once a second, and
  each copy carries a slightly different reading. Stripping the numbers out
  leaves the shape of the message, which is what makes two of them the same
  alert - the same idea the agent's own log uses. What comes back is one row
  per distinct problem, most recently seen first, carrying how many times it
  has been raised and the reading that tripped it last.
*/
function groupAlerts(raw) {
    const groups = new Map();

    raw.forEach((entry, index) => {
        const { time, message } = parseAlert(entry);
        const key = message.replace(/[-+]?\d+(?:\.\d+)?/g, '#');

        const existing = groups.get(key);

        if (existing) {
            existing.count += 1;
            existing.message = message;
            existing.time = time;
            existing.index = index;
            return;
        }

        groups.set(key, {
            key,
            message,
            time,
            count: 1,
            index,
            ...classify(message),
        });
    });

    return [...groups.values()].sort((a, b) => b.index - a.index);
}

const WORST = { critical: 3, warn: 2, info: 1 };

function worstTone(groups) {
    return groups.reduce(
        (worst, group) => (WORST[group.tone] > WORST[worst] ? group.tone : worst),
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
                        Math.min(MAX_SPAN, metrics.columns),
                    );
                }

                if (dir === 's' || dir === 'se') {
                    size.height = clamp(
                        startH + (move.clientY - startY),
                        MIN_LIST_H,
                        MAX_LIST_H,
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

function buildCard(well) {
    const card = el.tplWell.content.firstElementChild.cloneNode(true);

    const name = card.querySelector('.well-name');

    name.textContent = well.database_name;

    // The address is still worth having, just not worth a line of every card.
    name.title = well.database_name + '  ' + well.ip_address;

    const remove = card.querySelector('.well-remove');

    // First click arms, second confirms. Avoids a browser dialog for something
    // that only stops monitoring and can be undone by adding the well back.
    remove.addEventListener('click', () => {
        if (remove.dataset.armed) {
            stopWell(well.database_name);
            return;
        }

        remove.dataset.armed = '1';
        setTimeout(() => delete remove.dataset.armed, 3000);
    });

    initResize(card, well.database_name);
    applySize(card, sizes[well.database_name]);

    return card;
}

function renderAlerts(list, groups) {
    list.replaceChildren();

    for (const group of groups) {
        const row = el.tplAlert.content.firstElementChild.cloneNode(true);

        row.dataset.tone = group.tone;
        row.querySelector('.alert-time').textContent = group.time;
        row.querySelector('.alert-msg').textContent = group.message;

        // Only when it has actually repeated - a lone alert says nothing.
        row.querySelector('.alert-repeat').textContent =
            group.count > 1 ? '\u00D7' + group.count : '';

        list.append(row);
    }
}

function updateCard(card, state, groups) {
    const list = card.querySelector('.alerts');
    const blank = card.querySelector('.well-blank-text');

    if (groups.length) {
        card.dataset.tone = worstTone(groups);
        delete card.dataset.blank;

        card.querySelector('.well-count').textContent =
            groups.length + (groups.length === 1 ? ' alert' : ' alerts');

        // Only redraw when something actually changed, so a card someone is
        // scrolling through does not jump under them every two seconds.
        const signature = groups.map((g) => g.key + ':' + g.count).join('|');

        if (state.signature !== signature) {
            state.signature = signature;
            renderAlerts(list, groups);
        }

        return;
    }

    // Nothing raised. Either the agent has not connected yet, or all is well.
    const starting = Date.now() - state.firstSeen < STARTING_MS;

    card.dataset.tone = 'ok';
    card.dataset.blank = '1';
    state.signature = '';
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

            cards.set(name, { card, firstSeen: Date.now(), signature: null });
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
            api('/alerts/' + encodeURIComponent(name) + '?limit=' + ALERT_LIMIT)
                .then((body) => groupAlerts(body.alerts || []))
                .catch(() => []),
        ),
    );

    let total = 0;

    names.forEach((name, index) => {
        const state = cards.get(name);

        if (!state) {
            return;
        }

        updateCard(state.card, state, results[index]);
        total += results[index].length;
    });

    el.statWells.textContent = names.length;
    el.statAlerts.textContent = total;
    el.statUpdated.textContent = 'updated ' + new Date().toLocaleTimeString();
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
        polling = setTimeout(startPolling, POLL_MS);
    });
}

// ---------------------------------------------------------------------------
// Adding and removing wells
// ---------------------------------------------------------------------------

async function stopWell(name) {
    try {
        await api('/wells/' + encodeURIComponent(name), { method: 'DELETE' });
        toast(name + ' is no longer monitored');
    } catch (err) {
        toast(err.message, 'error');
    }

    startPolling();
}

el.form.addEventListener('submit', async (event) => {
    event.preventDefault();

    const database_name = el.dbName.value.trim();
    const ip_address = el.ipAddress.value.trim();

    if (!database_name || !ip_address) {
        return;
    }

    el.submit.disabled = true;
    el.submit.textContent = 'Starting…';

    try {
        await api('/wells', {
            method: 'POST',
            body: JSON.stringify({ database_name, ip_address }),
        });

        toast(database_name + ' added — the agent starts within a few seconds');

        el.form.reset();
        closeModal();
        startPolling();
    } catch (err) {
        toast(err.message, 'error');
    } finally {
        el.submit.disabled = false;
        el.submit.textContent = 'Start monitoring';
    }
});

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

function openModal() {
    el.modal.hidden = false;
    el.dbName.focus();
}

function closeModal() {
    el.modal.hidden = true;
}

document.getElementById('settings-open').addEventListener('click', openModal);
document.getElementById('settings-close').addEventListener('click', closeModal);

for (const button of document.querySelectorAll('[data-open-settings]')) {
    button.addEventListener('click', openModal);
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
