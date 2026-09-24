/*
  What the dashboard and the log page both need.

  index.html and logs.html are two separate pages served from the same origin,
  and each one loads this first. It holds only what both of them use - where
  the API is, how to call it, and how to read and colour one alert - so the
  two pages cannot drift into disagreeing about what an alert says or which
  port to ask.

  A plain script, not a module: the names below are top-level declarations, so
  every script loaded after this one sees them.
*/

'use strict';

/*
  Where the API is.

  Normally nowhere: config_api.py serves this page and the endpoints it calls
  from the same app, so a relative path is already pointed at the right place.
  That is what follows CONFIG_API_PORT out of .env without being told what it
  is, and what keeps working behind a reverse proxy, on https, and under
  whatever hostname the server answers to.

  The other case is working on the page from a separate static server -
  "python -m http.server 5500" inside frontend/, VS Code Live Server, or the
  file opened off the disk. Those serve index.html and have no /wells of their
  own, so every call 404s and the dashboard sits on "No API" while the API is
  running perfectly well one port over.

  Both are handled by asking rather than assuming. GET /health is a route
  config_api.py always has, so one call to it at load says which case this is:

      it answers 200      this origin is the API   -> relative, as above
      it answers 404      files only, no API       -> DEV_API_PORT
      nothing answers     the API is down, not     -> relative, so it is
                          absent - it lives here      picked up when it returns

  The port below is therefore only ever used once the origin has been shown
  not to be the API, which is what keeps it from repeating the bug where
  setting CONFIG_API_PORT=9000 served the page from :9000 and left it calling
  :8000. A deployment on any port is found by the probe and never reads it.

  ?api= still wins outright, for pointing at a rig on another machine:

      http://127.0.0.1:5500/index.html?api=http://10.0.0.5:8000

  Read per load and never stored, so a value typed once while developing
  cannot be left behind to misdirect a real deployment later. app.js hands it
  on to logs.html so the log tab opens against the same API.
*/

// Only read when the page is NOT served by the API. Change it alongside
// CONFIG_API_PORT in .env if you also work on the page from a static server.
const DEV_API_PORT = '8000';

const API_PARAM = new URLSearchParams(location.search).get('api') || '';

// Relative unless something proves otherwise, which is why this is not const.
let API_BASE = API_PARAM.replace(/\/+$/, '');

function devApiBase() {
    const scheme = location.protocol === 'https:' ? 'https:' : 'http:';

    // No hostname at all on file://, where localhost is the only sensible
    // guess; otherwise the same machine the page came from, so opening the
    // dashboard on a colleague's IP reaches that machine's API and not yours.
    return scheme + '//' + (location.hostname || 'localhost') + ':' + DEV_API_PORT;
}

/*
  Work out API_BASE once, before the first real call.

  A throw is deliberately not treated the same as a 404. Nothing answering
  means the origin this page came from is not serving anything right now -
  the API restarting, most likely - and it will come back at the same address,
  so staying relative is right. A 404 is something answering that is not the
  API, which only a file server does, and that is the case worth redirecting.
*/
const apiReady = (async () => {
    // Named on the URL: nothing to work out, and no probe worth spending.
    if (API_PARAM) {
        return;
    }

    // file:// has no origin to probe - a relative fetch cannot even be tried.
    if (location.protocol === 'file:') {
        API_BASE = devApiBase();
        return;
    }

    try {
        if ((await fetch('/health')).ok) {
            return;
        }
    } catch (err) {
        return;
    }

    API_BASE = devApiBase();

    console.info('This page is not served by the API; using ' + API_BASE);
})();

async function api(path, options) {
    // Every call waits on the one probe above, so nothing has to be sequenced
    // by hand in app.js or logs.js - they carry on calling api() as they did.
    await apiReady;

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
// One alert: "[11-09-26 17-20-28] ROP increased by 42.10%, BD-104.5" - a
// timestamp, then the sentence.
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
    [/increased by/i, 'critical'],
    [/Bit Depth jump by/i, 'critical'],
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

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}
