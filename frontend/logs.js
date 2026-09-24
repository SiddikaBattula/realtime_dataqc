/*
  The log page's own logic.

  It is given nothing but its URL. Everything it shows it fetches itself:

      GET /settings                 how often to poll, from .env
      GET /alerts/all/{well}        every alert that well has ever raised

  This is the whole point of the page being a page. It used to be generated in
  the dashboard tab and written into this one, which meant the tab held no
  instructions of its own - pressing F5 threw the written document away and
  asked the server for logs.html, and what came back rendered nothing. Now the
  browser reloads it the way it reloads anything else.

  Three things it has to do that a plain dump of the file does not:

    - newest first, because the file is append-only and nobody wants to scroll
      to the bottom of 800 lines to see what just happened
    - one line per alert, not a bordered box each - a box per row is fine for a
      handful of alerts and unusable for hundreds
    - live: the tab stays open on a rig console for a shift, so it polls the
      same way the dashboard does and prepends only what is new

  shared.js supplies api(), parseAlert(), classify() and escapeHtml().
*/

'use strict';

const el = {
    wellName: document.getElementById('well-name'),
    filter: document.getElementById('filter'),
    list: document.getElementById('list'),
    chips: [...document.querySelectorAll('.chip')],
};

// Which well this tab is showing, out of ?well= - the one thing the dashboard
// tells it.
const WELL = new URLSearchParams(location.search).get('well');

// Milliseconds between polls. The fallback is what the page runs on until
// GET /settings answers, and if it never does - the same arrangement app.js
// uses, so a server too old for the endpoint still gets a working page.
let pollMs = 5000;

let pollTimer = null;

// The raw alerts as the last poll saw them. Kept so a poll can tell an
// ordinary append from a file that has been pruned underneath it - see
// isPureAppend().
let known = [];

const activeTones = new Set();

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

/* One alert, as one dense line - time, then the agent's own sentence, tinted
   by classify() the same way a well card's rows are. */
function rowHtml(raw) {
    const { time, message } = parseAlert(String(raw));
    const { tone } = classify(message);

    return `<div class="row" data-tone="${tone}">`
        + `<span class="time">${escapeHtml(time || '-')}</span>`
        + `<span class="msg">${escapeHtml(message)}</span>`
        + `</div>`;
}

/* The whole list replaced by one sentence - nothing recorded yet, or a reason
   the page cannot show anything. */
function notice(text, tone) {
    el.list.innerHTML =
        `<p class="empty-msg"${tone ? ` data-tone="${tone}"` : ''}>`
        + escapeHtml(text)
        + `</p>`;
}

/* Everything, newest first. Used for the first load and whenever the file has
   changed in a way that is not a plain append. */
function renderAll(alerts) {
    if (!alerts.length) {
        notice('No alerts recorded for this well.');
        return;
    }

    // Reversed here so the poll below only ever has to *prepend* - the row
    // order never has to be recomputed.
    el.list.innerHTML = [...alerts].reverse().map(rowHtml).join('');

    applyFilter();
}

/* The alerts that arrived since the last poll, put on top and flashed once. */
function prepend(fresh) {
    const empty = el.list.querySelector('.empty-msg');

    if (empty) {
        empty.remove();
    }

    el.list.insertAdjacentHTML(
        'afterbegin',
        [...fresh].reverse().map(rowHtml).join(''),
    );

    for (const row of [...el.list.children].slice(0, fresh.length)) {
        row.classList.add('is-new');
    }

    applyFilter();
}

// ---------------------------------------------------------------------------
// Filtering - a time or a time range, and the tone chips
// ---------------------------------------------------------------------------

/*
  The end of a range, as HH:MM:SS.

  "13:55" means all of that minute, so it is compared as 13:55:59. Left as
  typed, a string comparison reads "13:55:30" as greater than "13:55" and
  drops it out of a range that plainly says it is in.
*/
function rangeEnd(text) {
    const parts = text.split(':');

    while (parts.length < 3) {
        parts.push('59');
    }

    return parts.join(':');
}

function applyFilter() {
    const query = el.filter.value.trim();

    let startTime = null;
    let endTime = null;

    if (query.includes('-')) {
        const parts = query.split('-');

        startTime = parts[0].trim();
        endTime = rangeEnd(parts[1].trim());
    }

    for (const row of el.list.querySelectorAll('.row')) {
        const timeText = (row.querySelector('.time')?.textContent || '').trim();

        let matchesText = true;

        if (startTime && endTime) {
            matchesText = timeText >= startTime && timeText <= endTime;
        } else if (query) {
            matchesText = timeText.startsWith(query);
        }

        const matchesTone =
            activeTones.size === 0 || activeTones.has(row.dataset.tone);

        row.style.display = matchesText && matchesTone ? '' : 'none';
    }
}

el.filter.addEventListener('input', applyFilter);

for (const chip of el.chips) {
    chip.addEventListener('click', () => {
        const tone = chip.dataset.tone;

        if (activeTones.has(tone)) {
            activeTones.delete(tone);
            chip.removeAttribute('data-active');
        } else {
            activeTones.add(tone);
            chip.dataset.active = '1';
        }

        applyFilter();
    });
}

// ---------------------------------------------------------------------------
// Loading and polling
// ---------------------------------------------------------------------------

/*
  Whether `alerts` is what we already had with more on the end.

  The agent appends alerts and sweeps old ones off the front
  (ALERT_RETENTION_HOURS), so the list is not simply ever-growing. Counting
  what was seen last time is not enough: after a sweep the count can fall, or
  land back where it was, and the rows already drawn no longer line up with
  the tail. Checking that the alert we last had on top is still where it was
  tells an append from a sweep, and a sweep just redraws.
*/
function isPureAppend(alerts) {
    if (!known.length) {
        return false;
    }

    if (alerts.length < known.length) {
        return false;
    }

    return alerts[known.length - 1] === known[known.length - 1];
}

async function load({ first = false } = {}) {
    const data = await api('/alerts/all/' + encodeURIComponent(WELL));
    const alerts = (data.alerts || []).map(String);

    if (first || !isPureAppend(alerts)) {
        renderAll(alerts);
    } else if (alerts.length > known.length) {
        prepend(alerts.slice(known.length));
    }

    known = alerts;
}

/* One request in flight at a time: the next poll is scheduled when the last
   one has finished, so a slow server queues nothing up behind it. */
function poll() {
    clearTimeout(pollTimer);

    load()
        .catch(() => {
            // Quiet - the page just does not update until a poll succeeds,
            // the same as the dashboard's own polling.
        })
        .finally(() => {
            pollTimer = setTimeout(poll, pollMs);
        });
}

async function loadPollInterval() {
    try {
        const settings = await api('/settings');

        if (settings && settings.poll_seconds) {
            pollMs = settings.poll_seconds * 1000;
        }
    } catch (err) {
        console.warn(
            'Using the built-in poll interval; GET /settings failed:',
            err.message,
        );
    }
}

async function start() {
    if (!WELL) {
        el.wellName.textContent = 'No well chosen';
        notice('Open this page from a well\'s Show Logs button.', 'error');
        return;
    }

    document.title = WELL + ' - Alert Log';
    el.wellName.textContent = WELL;

    await loadPollInterval();

    try {
        await load({ first: true });
    } catch (err) {
        notice('Could not load logs: ' + err.message, 'error');
    }

    pollTimer = setTimeout(poll, pollMs);
}

// Polling stops while the tab is hidden and catches up when it comes back: a
// log left open on a second monitor all shift is the normal case, and there is
// nothing to see in a tab nobody is looking at.
document.addEventListener('visibilitychange', () => {
    if (!WELL) {
        return;
    }

    if (document.hidden) {
        clearTimeout(pollTimer);
    } else {
        poll();
    }
});

start();
