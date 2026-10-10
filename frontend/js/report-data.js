/*
  Turns output/<well>/alerts.json into the numbers the report shows.

  No DOM and no PDF here - report.js draws it on screen and report-pdf.js
  writes it to a file, and both read the same summary so the two can never
  disagree.

  Every alert is "[dd-mm-yy HH-MM-SS] <sentence>", but each check words its
  sentence differently and puts the bit depth in a different place:

      H2S : 80.00ppm above limit 50ppm  BD : 2499.95m          range
      RPM is 0 where BD:202.61m, MD:1551.01m                    zero value
      ROP increased by 80.00%, BD:2499.94m                      ROP change
      TA is greater than TG where BD:2499.95                    TA > TG
      last depth : 12.00 | current depth : 30.00 | Bit Depth jump by 18.00m
      Please check for data Trans. Hookload has remained unchanged for 30 seconds

  Zero values and bit-depth jumps are saved once, when they start, and closed
  by one more alert when they stop (activity_check.py, bit_depth_check.py):

      RPM has resumed (was 0 for 127 seconds), BD:2499.95m
      Bit depth steady for 30 seconds (was jumping for 25 seconds, 3 jump(s), largest 8.01m)

  A closing alert joins its opening alert's row. The seconds in it say when
  the condition started and ended - ended the moment it resumed, or for the
  bit depth that many "steady" seconds before the alert - which is what the
  report's Time Duration is measured by. It is not counted as an alert itself.

  So each shape has its own rule below. The rule names the problem without
  its numbers ("H2S above limit"), which is what groups a hundred readings of
  one standing problem into one row, and pulls the numbers out separately so
  the row can say what range they covered.

  A plain script, not a module: ReportData is read by report.js and
  report-pdf.js, loaded after it.
*/
'use strict';

const ReportData = (function () {
    // Alerts of one kind closer together than this are one episode: the same
    // problem still going on, not a new occurrence of it.
    const EPISODE_GAP_MINUTES = 15;

    const N = '(-?\\d+(?:\\.\\d+)?)';
    const STAMP = /^\[(\d{2})-(\d{2})-(\d{2}) (\d{2})-(\d{2})-(\d{2})\]\s*([\s\S]*)$/;
    const BD = new RegExp(`\\bBD\\s*:\\s*${N}\\s*([a-z]*)`, 'i');
    const MD = new RegExp(`\\bMD\\s*:\\s*${N}\\s*([a-z]*)`, 'i');


    // [category, pattern, (match) => fields]. First match wins, so the more
    // specific shapes come first. `title` is the alert as the dashboard card
    // words it, with only its numbers left out ("RPM is 0", not "RPM is 0
    // where BD:2500.36m"); `value`/`unit` is the reading worth reporting a
    // range of.
    const RULES = [
        ['Out of range',
            new RegExp(`^(.+?)\\s*:\\s*${N}(\\S*?)\\s+(above|below) limit ${N}(\\S*)`, 'i'),
            (m) => ({
                title: `${m[1]} ${m[4].toLowerCase()} limit`,
                value: +m[2], unit: m[3] || m[6], limit: +m[5],
            })],

        // Titled like the alert they close, so the two share a row. Only the
        // report reads these; the card and Show Logs leave them out.
        ['Zero value',
            new RegExp(`^(.+?) has resumed \\(was 0 for ${N} seconds\\)`, 'i'),
            (m) => ({ title: `${m[1]} is 0`, closing: true, lasted: +m[2], quiet: 0 })],

        ['Bit depth jump',
            new RegExp(`^Bit depth steady for ${N} seconds \\(was jumping for ${N} seconds`, 'i'),
            (m) => ({ title: 'Bit Depth jump', closing: true, lasted: +m[2], quiet: +m[1] })],

        // The same two, as a test build on 10-10-26 worded them.
        ['Zero value',
            new RegExp(`^(.+?) resumed at [\\d:]+ - was 0 from .*?\\(${N}s\\)`, 'i'),
            (m) => ({ title: `${m[1]} is 0`, closing: true, lasted: +m[2], quiet: 0 })],

        ['Bit depth jump',
            new RegExp(`^Bit depth steady again since [\\d:]+ - was jumping from .*?\\(${N}s\\)`, 'i'),
            (m) => ({ title: 'Bit Depth jump', closing: true, lasted: +m[1], quiet: 30 })],

        ['Zero value',
            /^(.+?) is 0 where/i,
            (m) => ({ title: `${m[1]} is 0` })],

        // The wording before "is 0 where", still in files from older agents.
        ['Zero value',
            /^(.+?) cannot be 0 in (.+?) where/i,
            (m) => ({ title: `${m[1]} cannot be 0 in ${m[2].toUpperCase()}` })],

        ['Rate of penetration',
            new RegExp(`^(.+?) is ${N}(\\S*?)\\s*\\(Avg:\\s*${N}.*?threshold of ${N}%`, 'i'),
            (m) => ({
                title: `${m[1]} exceeds the configured threshold of ${m[5]}%`,
                value: +m[2], unit: m[3],
            })],

        ['Rate of penetration',
            new RegExp(`^(.+?) increased by ${N}%`, 'i'),
            (m) => ({ title: `${m[1]} increased`, value: +m[2], unit: '%' })],

        ['TA > TG',
            /^(.+?) is greater than (.+?) where/i,
            (m) => ({ title: `${m[1]} is greater than ${m[2]}` })],

        ['Bit depth jump',
            new RegExp(`Bit Depth jump by ${N}\\s*([a-z]*)`, 'i'),
            (m, text) => {
                const current = new RegExp(`current depth\\s*:\\s*${N}`, 'i').exec(text);
                return {
                    title: 'Bit Depth jump',
                    value: +m[1], unit: m[2] || 'm',
                    bd: current ? +current[1] : null,
                };
            }],

        ['Data feed',
            new RegExp(`data Trans\\.?\\s*(.+?) has remained unchanged for ${N} seconds`, 'i'),
            (m) => ({ title: `Please check for data Trans. ${m[1]} has remained unchanged`, value: +m[2], unit: 's' })],

        ['Data feed',
            new RegExp(`Realtime data feed has stopped for the last ${N}`, 'i'),
            (m) => ({ title: 'Realtime data feed has stopped', value: +m[1], unit: 's' })],

        ['Data feed',
            new RegExp(`^(?:Check in data feed\\.\\s*)?(.+?) is STILL unchanged at ${N} - stuck for ${N} min`, 'i'),
            (m) => ({
                title: `Check in data feed. ${m[1]} is still unchanged`,
                value: +m[3], unit: 'min',
            })],

        ['Data feed',
            new RegExp(`Realtime data feed has resumed.*?for ${N} seconds`, 'i'),
            (m) => ({ title: 'Realtime data feed has resumed', value: +m[1], unit: 's' })],

        ['SPP vs pump rate',
            new RegExp(`^(.+?) out of range(?: from ${N}s)?`, 'i'),
            (m) => ({
                title: `${m[1]} out of range`,
                value: m[2] === undefined ? null : +m[2], unit: 's',
            })],

        ['Activity',
            /^Cannot determine activity/i,
            () => ({ title: 'Cannot determine activity' })],

        ['Activity',
            /^Unknown activity:\s*(.+)$/i,
            (m) => ({ title: `Unknown activity: ${m[1].trim()}` })],
    ];

    // Same tones the dashboard colours its rows with (shared.js), so a row
    // that is red on a card is "Critical" in the report too.
    const SEVERITY = { critical: 'Critical', warn: 'Warning', info: 'Warning' };
    const SEVERITY_RANK = { Critical: 0, Warning: 1, Info: 2 };

    function parseOne(raw) {
        const m = STAMP.exec(String(raw).trim());
        if (!m) return null;

        const [, dd, mo, yy, hh, mi, ss, text] = m;
        const time = new Date(2000 + +yy, +mo - 1, +dd, +hh, +mi, +ss);
        if (Number.isNaN(time.getTime())) return null;

        const bd = BD.exec(text);
        const md = MD.exec(text);

        let fields = null;
        let category = 'Other';

        for (const [name, pattern, read] of RULES) {
            const hit = pattern.exec(text);
            if (hit) {
                category = name;
                fields = read(hit, text);
                break;
            }
        }

        if (!fields) {
            // Unknown shape: the sentence as the card shows it, less the bit
            // depth on the end. Its numbers stay - masking them as "#" made
            // rows nobody could read.
            fields = {
                title: text.replace(/[\s,]*(where\s+)?BD\s*:.*$/i, '').trim() || text,
            };
        }

        const tone = typeof classify === 'function' ? classify(text).tone : 'info';

        const closing = Boolean(fields.closing) && Number.isFinite(fields.lasted);

        // What the condition covered. A closing alert works it out back from
        // its own time: it ended `quiet` seconds before the alert and lasted
        // `lasted` seconds. Every other alert covers the moment it was raised.
        const end = closing ? new Date(time - (fields.quiet || 0) * 1000) : time;
        const start = closing ? new Date(end - fields.lasted * 1000) : time;

        return {
            time,
            start,
            end,
            closing,
            text,
            category,
            title: fields.title,
            value: Number.isFinite(fields.value) ? fields.value : null,
            unit: fields.unit || '',
            limit: Number.isFinite(fields.limit) ? fields.limit : null,
            bd: fields.bd != null ? fields.bd : (bd ? +bd[1] : null),
            md: md ? +md[1] : null,
            depthUnit: (bd && bd[2]) || (md && md[2]) || 'm',
            severity: SEVERITY[tone] || 'Info',
        };
    }

    function parse(lines) {
        return lines.map(parseOne).filter(Boolean).sort((a, b) => a.time - b.time);
    }

    // ------------------------------------------------------------------
    // Small numeric helpers
    // ------------------------------------------------------------------
    const pad = (n) => String(n).padStart(2, '0');

    function stamp(t) {
        return `${pad(t.getDate())}-${pad(t.getMonth() + 1)}-${t.getFullYear()} `
            + `${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
    }

    function duration(ms) {
        const s = Math.round(ms / 1000);
        if (s < 60) return `${s}s`;
        const m = Math.floor(s / 60);
        if (m < 60) return `${m}m ${pad(s % 60)}s`;
        const h = Math.floor(m / 60);
        return `${h}h ${pad(m % 60)}m`;
    }

    // The Time Duration of Alerts by type, to the second: "2h 57m 55s".
    // `duration` above rounds off for the narrower timeline column.
    function formatDuration(ms) {
        const totalSeconds = Math.floor(ms / 1000);

        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;

        const parts = [];

        if (hours) parts.push(`${hours}h`);
        if (minutes) parts.push(`${minutes}m`);
        if (seconds || parts.length === 0) parts.push(`${seconds}s`);

        return parts.join(' ');
    }

    // "12.40 - 98.10 ppm", "50 ppm", or "-" when there was nothing to show.
    function span(values, unit) {
        const v = values.filter((x) => x !== null);
        if (!v.length) return '-';
        const lo = Math.min(...v), hi = Math.max(...v);
        const u = unit ? ` ${unit}` : '';
        // Both ends written alike: "2460.00 - 2460.81", never "2460 - 2460.81".
        const f = v.every(Number.isInteger) ? String : (x) => x.toFixed(2);
        return lo === hi ? `${f(lo)}${u}` : `${f(lo)} - ${f(hi)}${u}`;
    }

    function episodesOf(rows) {
        const gap = EPISODE_GAP_MINUTES * 60 * 1000;
        const out = [];
        let current = null;

        // By when each condition started, so a closing alert lands in the
        // episode its opening alert began, however long that ran.
        for (const r of [...rows].sort((a, b) => a.start - b.start)) {
            if (!current || r.start - current.last > gap) {
                current = { first: r.start, last: r.end, rows: [] };
                out.push(current);
            }
            if (r.end > current.last) current.last = r.end;
            current.rows.push(r);
        }

        for (const ep of out) ep.duration = lastedOf(ep.rows, ep.last - ep.first);
        return out;
    }

    // How long a set of alerts was actually standing, in ms. Where closing
    // alerts say how long each spell lasted ("was 0 for 60 seconds"), their
    // seconds added up - two 1-minute spells ten minutes apart are 2 minutes,
    // not the 11 between the first and the last. Otherwise `fallback`, the
    // first to last alert, as for every other kind of alert.
    function lastedOf(rows, fallback) {
        const closing = rows.filter((r) => r.closing);
        if (!closing.length) return fallback;
        return closing.reduce((total, r) => total + (r.end - r.start), 0);
    }

    // ------------------------------------------------------------------
    // The summary both the screen and the PDF draw
    // ------------------------------------------------------------------
    function summarise(rows) {
        const groups = new Map();
        const byHour = Array(24).fill(0);
        const days = new Set();

        for (const r of rows) {
            const key = `${r.category}|${r.title}`;
            let g = groups.get(key);
            if (!g) {
                g = {
                    category: r.category, title: r.title, severity: r.severity,
                    unit: r.unit, depthUnit: r.depthUnit, rows: [],
                };
                groups.set(key, g);
            }
            g.rows.push(r);
            if (SEVERITY_RANK[r.severity] < SEVERITY_RANK[g.severity]) g.severity = r.severity;

            // A closing alert says when its row ended; it is not another alert.
            if (r.closing) continue;

            byHour[r.time.getHours()]++;
            days.add(r.time.toDateString());
        }

        const list = [...groups.values()].map((g) => {
            const episodes = episodesOf(g.rows);
            const limits = g.rows.map((r) => r.limit);
            const limitText = span(limits, g.unit);

            let reading = span(g.rows.map((r) => r.value), g.unit);
            if (limitText !== '-') reading += ` (limit ${limitText})`;

            return {
                category: g.category,
                title: g.title,
                severity: g.severity,
                count: g.rows.filter((r) => !r.closing).length,
                episodes,
                // The "Time Duration" of Alerts by type, on screen and in the PDF.
                duration: episodes.reduce((total, ep) => total + ep.duration, 0),
                first: new Date(Math.min(...g.rows.map((r) => r.start))),
                last: new Date(Math.max(...g.rows.map((r) => r.end))),
                reading,
                bitDepth: span(g.rows.map((r) => r.bd), g.depthUnit),
                holeDepth: span(g.rows.map((r) => r.md), g.depthUnit),
            };
        }).sort((a, b) =>
            SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || b.count - a.count);

        // Every episode of every problem, in the order they started.
        const timeline = list.flatMap((g) => g.episodes.map((ep) => ({
            first: ep.first,
            last: ep.last,
            duration: ep.duration,
            title: g.title,
            category: g.category,
            severity: g.severity,
            count: ep.rows.filter((r) => !r.closing).length,
            bitDepth: span(ep.rows.map((r) => r.bd), ep.rows[0].depthUnit),
        }))).sort((a, b) => a.first - b.first);

        const peak = Math.max(...byHour);
        const raised = rows.filter((r) => !r.closing);

        return {
            total: raised.length,
            first: rows.length ? new Date(Math.min(...rows.map((r) => r.start))) : null,
            last: rows.length ? new Date(Math.max(...rows.map((r) => r.end))) : null,
            days: days.size,
            critical: raised.filter((r) => r.severity === 'Critical').length,
            groups: list,
            timeline,
            peakHour: peak > 0 ? byHour.indexOf(peak) : null,
            peakCount: peak,
            bitDepthRange: span(rows.map((r) => r.bd), rows.length ? rows[0].depthUnit : 'm'),
            holeDepthRange: span(rows.map((r) => r.md), rows.length ? rows[0].depthUnit : 'm'),
            hasHoleDepth: rows.some((r) => r.md !== null),
        };
    }

    return {
        EPISODE_GAP_MINUTES,
        parse,
        summarise,
        stamp,
        duration,
        formatDuration,
        pad,
    };
})();
