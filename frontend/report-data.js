/*
  Turns output/<well>/alerts.json into the numbers the report shows.

  No DOM and no PDF here - report.js draws it on screen and report-pdf.js
  writes it to a file, and both read the same summary so the two can never
  disagree.

  Every alert is "[dd-mm-yy HH-MM-SS] <sentence>", but each check words its
  sentence differently and puts the bit depth in a different place:

      H2S : 80.00ppm above limit 50ppm  BD : 2499.95m          range
      RPM cannot be 0 in DRILLING where BD:202.61m, MD:1551.01m zero value
      ROP increased by 80.00%, BD:2499.94m                      ROP change
      TA is greater than TG where BD:2499.95                    TA > TG
      last depth : 12.00 | current depth : 30.00 | Bit Depth jump by 18.00m
      Please check for data Trans. Hookload has remained unchanged for 30 seconds

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
    // specific shapes come first. `title` is the problem without its numbers;
    // `value`/`unit` is the reading worth reporting a range of.
    const RULES = [
        ['Out of range',
            new RegExp(`^(.+?)\\s*:\\s*${N}(\\S*?)\\s+(above|below) limit ${N}(\\S*)`, 'i'),
            (m) => ({
                title: `${m[1]} ${m[4].toLowerCase()} limit`,
                value: +m[2], unit: m[3] || m[6], limit: +m[5],
            })],

        ['Zero value',
            /^(.+?) cannot be 0 in (.+?) where/i,
            (m) => ({ title: `${m[1]} reading 0 while ${m[2].toUpperCase()}` })],

        ['Rate of penetration',
            new RegExp(`^(.+?) is ${N}(\\S*?)\\s*\\(Avg:\\s*${N}.*?threshold of ${N}%`, 'i'),
            (m) => ({
                title: `${m[1]} above its rolling average by more than ${m[5]}%`,
                value: +m[2], unit: m[3],
            })],

        ['Rate of penetration',
            new RegExp(`^(.+?) increased by ${N}%`, 'i'),
            (m) => ({ title: `${m[1]} increase`, value: +m[2], unit: '%' })],

        ['TA > TG',
            /^(.+?) is greater than (.+?) where/i,
            (m) => ({ title: `${m[1]} greater than ${m[2]}` })],

        ['Bit depth jump',
            new RegExp(`Bit Depth jump by ${N}\\s*([a-z]*)`, 'i'),
            (m, text) => {
                const current = new RegExp(`current depth\\s*:\\s*${N}`, 'i').exec(text);
                return {
                    title: 'Bit depth jump',
                    value: +m[1], unit: m[2] || 'm',
                    bd: current ? +current[1] : null,
                };
            }],

        ['Data feed',
            new RegExp(`data Trans\\.?\\s*(.+?) has remained unchanged for ${N} seconds`, 'i'),
            (m) => ({ title: `${m[1]} unchanged - feed may be frozen`, value: +m[2], unit: 's' })],

        ['Data feed',
            new RegExp(`Realtime data feed has stopped for the last ${N}`, 'i'),
            (m) => ({ title: 'Real-time data feed stopped', value: +m[1], unit: 's' })],

        ['Data feed',
            new RegExp(`(.+?) is STILL unchanged at ${N} - stuck for ${N} min`, 'i'),
            (m) => ({
                title: 'Real-time data feed still stopped (reminder)',
                value: +m[3], unit: 'min',
            })],

        ['Data feed',
            new RegExp(`Realtime data feed has resumed.*?for ${N} seconds`, 'i'),
            (m) => ({ title: 'Real-time data feed resumed', value: +m[1], unit: 's' })],

        ['SPP vs pump rate',
            new RegExp(`^(.+?) out of range(?: from ${N}s)?`, 'i'),
            (m) => ({
                title: `${m[1]} outside expected range for pump rate`,
                value: m[2] === undefined ? null : +m[2], unit: 's',
            })],

        ['Activity',
            /^Cannot determine activity/i,
            () => ({ title: 'Activity could not be determined (depth missing)' })],

        ['Activity',
            /^Unknown activity:\s*(.+)$/i,
            (m) => ({ title: `Unknown activity "${m[1].trim()}"` })],
    ];

    // Same tones the dashboard colours its rows with (shared.js), so a row
    // that is red on a card is "Critical" in the report too.
    const SEVERITY = { critical: 'Critical', warn: 'Warning', info: 'Info' };
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
            // Unknown shape: numbers blanked so repeats still group together.
            fields = {
                title: text.replace(/\s*(where\s+)?BD\s*:.*$/i, '')
                    .replace(/-?\d+(\.\d+)?/g, '#').trim() || text,
            };
        }

        const tone = typeof classify === 'function' ? classify(text).tone : 'info';

        return {
            time,
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

        for (const r of rows) {
            if (!current || r.time - current.last > gap) {
                current = { first: r.time, last: r.time, rows: [] };
                out.push(current);
            }
            current.last = r.time;
            current.rows.push(r);
        }
        return out;
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
                count: g.rows.length,
                episodes,
                first: g.rows[0].time,
                last: g.rows[g.rows.length - 1].time,
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
            title: g.title,
            category: g.category,
            severity: g.severity,
            count: ep.rows.length,
            bitDepth: span(ep.rows.map((r) => r.bd), ep.rows[0].depthUnit),
        }))).sort((a, b) => a.first - b.first);

        const peak = Math.max(...byHour);

        return {
            total: rows.length,
            first: rows.length ? rows[0].time : null,
            last: rows.length ? rows[rows.length - 1].time : null,
            days: days.size,
            critical: rows.filter((r) => r.severity === 'Critical').length,
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
        pad,
    };
})();
