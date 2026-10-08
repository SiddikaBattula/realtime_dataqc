// /* Report button: summarises output/<well>/alerts.json for the well whose
//    rules form is open. Needs shared.js first (api, escapeHtml).

//    Log line format:
//    "[dd-mm-yy HH-MM-SS] <message> where BD:202.61m, MD:1551.01m"
// */
// (function () {
//     // Full output/<well>/alerts.json, no limit or age filter (see
//     // get_all_alerts in the config API). Returns {database_name, count, alerts}.
//     const alertsUrl = (well) => `/alerts/all/${encodeURIComponent(well)}`;

//     // Logo is served by the frontend from D:\realtime_dataqc\frontend\
//     // Change this if your server exposes it under a different URL.
//     const LOGO_URL = '/logo-default-223x59.png';

//     const LINE = /^\[(\d{2})-(\d{2})-(\d{2}) (\d{2})-(\d{2})-(\d{2})\]\s*(.*)$/;
//     const DEPTHS = /\s*where\s+BD:\s*([\d.]+)m?,\s*MD:\s*([\d.]+)m?\s*$/i;

//     function parse(lines) {
//         const out = [];
//         for (const raw of lines) {
//             const m = LINE.exec(String(raw).trim());
//             if (!m) continue;
//             const [, dd, mm, yy, hh, mi, ss, rest] = m;
//             const d = DEPTHS.exec(rest);
//             out.push({
//                 t: new Date(2000 + +yy, +mm - 1, +dd, +hh, +mi, +ss),
//                 msg: rest.replace(DEPTHS, '').trim(),
//                 bd: d ? parseFloat(d[1]) : null,
//                 md: d ? parseFloat(d[2]) : null,
//             });
//         }
//         return out.sort((a, b) => a.t - b.t);
//     }

//     const pad = (n) => String(n).padStart(2, '0');
//     const fmt = (t) => `${pad(t.getDate())}-${pad(t.getMonth() + 1)}-${String(t.getFullYear()).slice(2)} ${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
//     const day = (t) => `${pad(t.getDate())}-${pad(t.getMonth() + 1)}-${t.getFullYear()}`;
//     const esc = (s) => (window.escapeHtml ? escapeHtml(String(s)) : String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])));

//     function summarise(rows) {
//         const byMsg = new Map(), byDay = new Map(), byHour = Array(24).fill(0);
//         for (const r of rows) {
//             let g = byMsg.get(r.msg);
//             if (!g) byMsg.set(r.msg, g = { msg: r.msg, n: 0, first: r.t, last: r.t, bd: new Set(), md: new Set() });
//             g.n++; g.last = r.t;
//             if (r.bd !== null) g.bd.add(r.bd);
//             if (r.md !== null) g.md.add(r.md);
//             const d = day(r.t);
//             byDay.set(d, (byDay.get(d) || 0) + 1);
//             byHour[r.t.getHours()]++;
//         }
//         return { groups: [...byMsg.values()].sort((a, b) => b.n - a.n), byDay, byHour };
//     }

//     const range = (set) => {
//         if (!set.size) return '—';
//         const v = [...set].sort((a, b) => a - b);
//         return v.length === 1 ? `${v[0]} m` : `${v[0]} – ${v[v.length - 1]} m`;
//     };

//     function header(well, rows) {
//         const generated = fmt(new Date());
//         return `
//         <div class="rp-header">
//             <img class="rp-logo" src="${esc(LOGO_URL)}" alt="Logo" onerror="this.style.display='none'">
//             <div class="rp-header-text">
//                 <h2>Alerts Report</h2>
//                 <p><b>Well:</b> ${esc(well)} &nbsp;|&nbsp; <b>Generated:</b> ${esc(generated)}</p>
//             </div>
//         </div>`;
//     }

//     function html(well, rows) {
//         if (!rows.length) return header(well, rows) + '<p class="modal-intro">No alerts have been logged for this well.</p>';
//         const s = summarise(rows);
//         const peak = Math.max(...s.byHour, 1);
//         const peakHour = s.byHour.indexOf(peak);
//         const top = s.groups[0];
//         const first = rows[0].t, last = rows[rows.length - 1].t;
//         return `
//         ${header(well, rows)}

//         <div class="rp-cards">
//             <div><b>${rows.length}</b><span>Total alerts</span></div>
//             <div><b>${s.groups.length}</b><span>Alert types</span></div>
//             <div><b>${s.byDay.size}</b><span>Days with alerts</span></div>
//         </div>
//         <p class="rp-span">Period: ${esc(fmt(first))} &rarr; ${esc(fmt(last))}</p>

//         <h3 class="rp-h">By alert type</h3>
//         <table class="rp-table"><thead><tr><th>Alert</th><th>First</th><th>Last</th><th>Bit depth</th><th>Hole depth</th></tr></thead><tbody>
//         ${s.groups.map((g) => `<tr><td>${esc(g.msg)}</td><td>${esc(fmt(g.first))}</td><td>${esc(fmt(g.last))}</td><td>${range(g.bd)}</td><td>${range(g.md)}</td></tr>`).join('')}
//         </tbody></table>

//         <p class="rp-note">${esc(top.msg)} was the most frequent (${top.n} of ${rows.length}). Busiest hour: ${pad(peakHour)}:00&ndash;${pad(peakHour)}:59 (${peak} alerts).</p>`;
//     }

//     function csv(rows) {
//         const q = (v) => `"${String(v).replace(/"/g, '""')}"`;
//         return ['alert,count,first,last,bit_depth,hole_depth',
//             ...summarise(rows).groups.map((g) => [q(g.msg), g.n, q(fmt(g.first)), q(fmt(g.last)), q(range(g.bd)), q(range(g.md))].join(','))].join('\n');
//     }


//     const css = `
//     /* ---------- Header (screen + print) ---------- */
//     .rp-header{
//         display:flex;
//         align-items:center;
//         gap:18px;
//         margin-bottom:18px;
//         padding-bottom:14px;
//         border-bottom:2px solid var(--border,#555);
//     }
//     .rp-logo{height:48px;width:auto;flex:none}
//     .rp-header-text h2{margin:0;font-size:22px;font-weight:700;letter-spacing:.2px}
//     .rp-header-text p{margin:5px 0 0;font-size:13px;opacity:.9}

//     /* ---------- Summary cards ---------- */
//     .rp-cards{display:flex;gap:12px;margin-bottom:10px}
//     .rp-cards div{flex:1;padding:14px 12px;border:1px solid var(--border,#555);border-radius:8px;text-align:center}
//     .rp-cards b{display:block;font-size:28px;font-weight:700;line-height:1.2}
//     .rp-cards span{font-size:13px;font-weight:600;opacity:.85}
//     .rp-span{font-size:13px;font-weight:500;opacity:.9;margin:8px 0}

//     /* ---------- Table ---------- */
//     .rp-h{margin:20px 0 8px;font-size:16px;font-weight:700}
//     .rp-table{width:100%;border-collapse:collapse;font-size:13px}
//     .rp-table th{
//         text-align:left;padding:8px;font-weight:700;
//         background:rgba(128,128,128,.18);
//         border-bottom:2px solid var(--border,#666);
//     }
//     .rp-table td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--border,#3336);vertical-align:top}
//     .rp-table td:nth-child(n+2){white-space:nowrap}
//     .rp-table tbody tr:nth-child(even){background:rgba(128,128,128,.07)}
//     .rp-note{margin-top:18px;font-size:13px;font-weight:500}

//     /* ---------- Print: dark text on white ---------- */
//     @media print {
//         body > *:not(#report-modal) { display: none !important; }

//         #report-modal {
//             position: static !important;
//             inset: auto !important;
//             width: 100% !important;
//             height: auto !important;
//             overflow: visible !important;
//             background: #fff !important;
//         }
//         .modal-card {
//             width: 100% !important;
//             max-width: none !important;
//             height: auto !important;
//             max-height: none !important;
//             overflow: visible !important;
//             box-shadow: none !important;
//             border: none !important;
//             background: #fff !important;
//         }
//         .modal-body {
//             height: auto !important;
//             max-height: none !important;
//             overflow: visible !important;
//         }
//         .modal-head,
//         .modal-foot { display: none !important; }

//         /* Force dark text everywhere inside the report */
//         #report-modal,
//         #report-modal * {
//             color: #111 !important;
//             opacity: 1 !important;
//             -webkit-print-color-adjust: exact;
//             print-color-adjust: exact;
//         }

//         .rp-header { border-bottom: 2px solid #111; page-break-after: avoid; }
//         .rp-logo { height: 54px; }
//         .rp-header-text h2 { font-size: 22px; color: #000 !important; }
//         .rp-header-text p { font-size: 12px; color: #333 !important; }

//         .rp-cards div { border: 1px solid #555 !important; background: #fff !important; }
//         .rp-cards b { font-size: 26px; color: #000 !important; }
//         .rp-cards span { color: #333 !important; }
//         .rp-span { color: #333 !important; }

//         .rp-h { color: #000 !important; page-break-after: avoid; }

//         .rp-table { font-size: 11px; page-break-inside: auto; }
//         .rp-table th {
//             background: #e9ecef !important;
//             border-bottom: 2px solid #111 !important;
//             color: #000 !important;
//         }
//         .rp-table td { border-bottom: 1px solid #999 !important; }
//         .rp-table tbody tr:nth-child(even) { background: #f6f7f8 !important; }
//         .rp-table thead { display: table-header-group; }
//         .rp-table tr { page-break-inside: avoid; page-break-after: auto; }

//         h3 { page-break-after: avoid; }

//         @page { size: auto; margin: 10mm; }
//     }`;

//     function init() {
//         const logsBtn = document.getElementById('show-logs');
//         if (!logsBtn) return;

//         const style = document.createElement('style');
//         style.textContent = css;
//         document.head.appendChild(style);

//         const modal = document.createElement('div');
//         modal.className = 'modal';
//         modal.id = 'report-modal';
//         modal.hidden = true;
//         modal.innerHTML = `
//         <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="report-title">
//             <header class="modal-head"><h2 id="report-title">Report</h2>
//                 <button class="icon-btn" id="report-close" type="button" aria-label="Close">
//                     <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>
//                 </button></header>
//             <div class="modal-body" id="report-body"></div>
//             <footer class="modal-foot">
//                 <button class="btn" id="report-print" type="button">Print / PDF</button>
//             </footer>
//         </div>`;

//         // <button class="btn" id="report-csv" type="button">Download CSV</button>
//         document.body.appendChild(modal);

//         const body = modal.querySelector('#report-body');
//         let well = '', rows = [];


//         const btn = document.createElement('button');
//         btn.className = 'btn';
//         btn.id = 'show-report';
//         btn.type = 'button';
//         btn.textContent = 'Report';
//         btn.hidden = logsBtn.hidden;
//         logsBtn.after(btn);
//         // Follow Show Logs: visible only when it is (i.e. editing an existing well).
//         new MutationObserver(() => { btn.hidden = logsBtn.hidden; })
//             .observe(logsBtn, { attributes: true, attributeFilter: ['hidden'] });

//         btn.addEventListener('click', async () => {
//             well = document.getElementById('database_name').value.trim();
//             document.getElementById('report-title').textContent = `Report — ${well}`;
//             body.innerHTML = '<p class="modal-intro">Loading…</p>';
//             modal.hidden = false;
//             try {
//                 const data = await api(alertsUrl(well));
//                 rows = parse(Array.isArray(data) ? data : (data.alerts || []));
//                 body.innerHTML = html(well, rows);
//             } catch (e) {
//                 rows = [];
//                 body.innerHTML = `<p class="modal-error">Could not load alerts: ${esc(e.message || e)}</p>`;
//             }
//         });

//         modal.querySelector('#report-close').addEventListener('click', () => { modal.hidden = true; });
//         modal.addEventListener('click', (e) => { if (e.target === modal) modal.hidden = true; });
//         modal.querySelector('#report-print').addEventListener('click', () => window.print());

//         // CSV button is currently commented out in the footer, so guard against null.
//         const csvBtn = modal.querySelector('#report-csv');
//         if (csvBtn) csvBtn.addEventListener('click', () => {
//             const a = document.createElement('a');
//             a.href = URL.createObjectURL(new Blob([csv(rows)], { type: 'text/csv' }));
//             a.download = `${well}-alerts-report.csv`;
//             a.click();
//             URL.revokeObjectURL(a.href);
//         });
//     }

//     document.addEventListener('DOMContentLoaded', init);
// })();














/* Report button: summarises output/<well>/alerts.json for the well whose
   rules form is open. Needs shared.js first (api, escapeHtml).

   Log line format:
   "[dd-mm-yy HH-MM-SS] <message> where BD:202.61m, MD:1551.01m"
*/
(function () {
    // Full output/<well>/alerts.json, no limit or age filter (see
    // get_all_alerts in the config API). Returns {database_name, count, alerts}.
    const alertsUrl = (well) => `/alerts/all/${encodeURIComponent(well)}`;

    // Logo is served by the frontend from D:\realtime_dataqc\frontend\
    // Change this if your server exposes it under a different URL.
    const LOGO_URL = '/logo-default-223x59.png';

    const LINE = /^\[(\d{2})-(\d{2})-(\d{2}) (\d{2})-(\d{2})-(\d{2})\]\s*(.*)$/;
    const DEPTHS = /\s*where\s+BD:\s*([\d.]+)m?,\s*MD:\s*([\d.]+)m?\s*$/i;

    function parse(lines) {
        const out = [];
        for (const raw of lines) {
            const m = LINE.exec(String(raw).trim());
            if (!m) continue;
            const [, dd, mm, yy, hh, mi, ss, rest] = m;
            const d = DEPTHS.exec(rest);
            out.push({
                t: new Date(2000 + +yy, +mm - 1, +dd, +hh, +mi, +ss),
                msg: rest.replace(DEPTHS, '').trim(),
                bd: d ? parseFloat(d[1]) : null,
                md: d ? parseFloat(d[2]) : null,
            });
        }
        return out.sort((a, b) => a.t - b.t);
    }

    const pad = (n) => String(n).padStart(2, '0');
    const fmt = (t) => `${pad(t.getDate())}-${pad(t.getMonth() + 1)}-${String(t.getFullYear()).slice(2)} ${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
    const day = (t) => `${pad(t.getDate())}-${pad(t.getMonth() + 1)}-${t.getFullYear()}`;
    const esc = (s) => (window.escapeHtml ? escapeHtml(String(s)) : String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])));

    function summarise(rows) {
        const byMsg = new Map(), byDay = new Map(), byHour = Array(24).fill(0);
        for (const r of rows) {
            let g = byMsg.get(r.msg);
            if (!g) byMsg.set(r.msg, g = { msg: r.msg, n: 0, first: r.t, last: r.t, bd: new Set(), md: new Set() });
            g.n++; g.last = r.t;
            if (r.bd !== null) g.bd.add(r.bd);
            if (r.md !== null) g.md.add(r.md);
            const d = day(r.t);
            byDay.set(d, (byDay.get(d) || 0) + 1);
            byHour[r.t.getHours()]++;
        }
        return { groups: [...byMsg.values()].sort((a, b) => b.n - a.n), byDay, byHour };
    }

    const range = (set) => {
        if (!set.size) return '—';
        const v = [...set].sort((a, b) => a - b);
        return v.length === 1 ? `${v[0]} m` : `${v[0]} – ${v[v.length - 1]} m`;
    };

    function header(well, rows) {
        const first = rows[0].t, last = rows[rows.length - 1].t;
        return `
        <div class="rp-header">
            <img class="rp-logo" src="${esc(LOGO_URL)}" alt="Logo" onerror="this.style.display='none'">
            <div class="rp-header-text">
                <h2>Alerts Summary Report</h2>
                <p class="rp-well">
                    <b>Well:</b> ${esc(well)}
                </p>
                <p class="rp-span">
                    Period: ${esc(fmt(first))} → ${esc(fmt(last))}
                </p>
            </div>
        </div>`;
    }

    function html(well, rows) {
        if (!rows.length) return '<p class="modal-intro">No alerts have been logged for this well.</p>';
        const s = summarise(rows);
        const peak = Math.max(...s.byHour, 1);
        const peakHour = s.byHour.indexOf(peak);
        const top = s.groups[0];

        return `
        ${header(well, rows)}

        <div class="rp-cards">
            <div><b>${rows.length}</b><span>Total alerts</span></div>
            <div><b>${s.groups.length}</b><span>Alert types</span></div>
            <div><b>${s.byDay.size}</b><span>Days with alerts</span></div>
        </div>

        <table class="rp-table"><thead><tr><th>Alert</th><th>First</th><th>Last</th><th>Bit depth</th><th>Hole depth</th></tr></thead><tbody>
        ${s.groups.map((g) => `<tr><td>${esc(g.msg)}</td><td>${esc(fmt(g.first))}</td><td>${esc(fmt(g.last))}</td><td>${range(g.bd)}</td><td>${range(g.md)}</td></tr>`).join('')}
        </tbody></table>

        <p class="rp-note">${esc(top.msg)} was the most frequent (${top.n} of ${rows.length}). Busiest hour: ${pad(peakHour)}:00&ndash;${pad(peakHour)}:59 (${peak} alerts).</p>`;
    }

    function csv(rows) {
        const q = (v) => `"${String(v).replace(/"/g, '""')}"`;
        return ['alert,count,first,last,bit_depth,hole_depth',
            ...summarise(rows).groups.map((g) => [q(g.msg), g.n, q(fmt(g.first)), q(fmt(g.last)), q(range(g.bd)), q(range(g.md))].join(','))].join('\n');
    }


    const css = `
    /* ---------- Header (screen + print) ---------- */
    .rp-header {
        display: block;
        text-align: center;
        margin-bottom: 18px;
        padding-bottom: 14px;
        border-bottom: 2px solid var(--border,#555);
    }

    /* Logo is hidden on screen and only appears in the printed / PDF report */
    .rp-logo { display: none; }

    .rp-header-text { width: 100%; text-align: center; }
    .rp-header-text h2 {
        margin: 0;
        font-size: 28px;
        font-weight: 700;
        letter-spacing: .2px;
    }
    .rp-header-text p {
        margin: 5px 0 0;
        font-size: 18px;
        font-weight: 600;
    }

    /* ---------- Summary cards ---------- */
    .rp-cards{display:flex;gap:12px;margin-bottom:10px}
    .rp-cards div{flex:1;padding:14px 12px;border:1px solid var(--border,#555);border-radius:8px;text-align:center}
    .rp-cards b{display:block;font-size:28px;font-weight:700;line-height:1.2}
    .rp-cards span{font-size:13px;font-weight:600;opacity:.85}

    /* ---------- Table ---------- */
    .rp-h{margin:20px 0 8px;font-size:16px;font-weight:700}
    .rp-table{width:100%;border-collapse:collapse;font-size:13px}
    .rp-table th{
        text-align:left;padding:8px;font-weight:700;
        background:rgba(128,128,128,.18);
        border-bottom:2px solid var(--border,#666);
    }
    .rp-table td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--border,#3336);vertical-align:top}
    .rp-table td:nth-child(n+2){white-space:nowrap}
    .rp-table tbody tr:nth-child(even){background:rgba(128,128,128,.07)}
    .rp-note{margin-top:18px;font-size:13px;font-weight:500}

    .rp-span {
        font-size: 15px;
        margin: 4px 0;
    }

    /* ---------- Print: dark text on white ---------- */
    @media print {
        body > *:not(#report-modal) { display: none !important; }

        #report-modal {
            position: static !important;
            inset: auto !important;
            width: 100% !important;
            height: auto !important;
            overflow: visible !important;
            background: #fff !important;
        }
        .modal-card {
            width: 100% !important;
            max-width: none !important;
            height: auto !important;
            max-height: none !important;
            overflow: visible !important;
            box-shadow: none !important;
            border: none !important;
            background: #fff !important;
        }
        .modal-body {
            height: auto !important;
            max-height: none !important;
            overflow: visible !important;
        }
        .modal-head,
        .modal-foot { display: none !important; }

        /* Force dark text everywhere inside the report */
        #report-modal,
        #report-modal * {
            color: #111 !important;
            opacity: 1 !important;
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
        }

        .rp-header {
            display: block;
            position: relative;
            text-align: center;
            border-bottom: 2px solid #111;
            padding-bottom: 14px;
            page-break-after: avoid;
        }
        .rp-logo {
            display: block !important;
            position: absolute;
            left: 0;
            top: calc(50% - 7px);
            transform: translateY(-50%);
            height: 64px;
            width: auto;
            background: #fff !important;
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
        }
        .rp-header-text h2 { font-size: 22px; color: #000 !important; }
        .rp-header-text p { font-size: 12px; color: #333 !important; }
        .rp-header-text p.rp-well { font-size: 20px; font-weight: 400; color: #000 !important; margin: 8px 0 4px; }

        .rp-cards div { border: 1px solid #555 !important; background: #fff !important; }
        .rp-cards b { font-size: 26px; color: #000 !important; }
        .rp-cards span { color: #333 !important; }
        .rp-span { color: #333 !important; }

        .rp-h { color: #000 !important; page-break-after: avoid; }

        .rp-table { font-size: 11px; page-break-inside: auto; }
        .rp-table th {
            background: #e9ecef !important;
            border-bottom: 2px solid #111 !important;
            color: #000 !important;
        }
        .rp-table td { border-bottom: 1px solid #999 !important; }
        .rp-table tbody tr:nth-child(even) { background: #f6f7f8 !important; }
        .rp-table thead { display: table-header-group; }
        .rp-table tr { page-break-inside: avoid; page-break-after: auto; }

        h3 { page-break-after: avoid; }

        @page { size: auto; margin: 10mm; }
    }`;

    function init() {
        const logsBtn = document.getElementById('show-logs');
        if (!logsBtn) return;

        const style = document.createElement('style');
        style.textContent = css;
        document.head.appendChild(style);

        const modal = document.createElement('div');
        modal.className = 'modal';
        modal.id = 'report-modal';
        modal.hidden = true;
        modal.innerHTML = `
        <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="report-title">
            <header class="modal-head"><h2 id="report-title">Report</h2>
                <button class="icon-btn" id="report-close" type="button" aria-label="Close">
                    <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>
                </button></header>
            <div class="modal-body" id="report-body"></div>
            <footer class="modal-foot">
                <button class="btn" id="report-print" type="button">Print / PDF</button>
            </footer>
        </div>`;

        // <button class="btn" id="report-csv" type="button">Download CSV</button>
        document.body.appendChild(modal);

        const body = modal.querySelector('#report-body');
        let well = '', rows = [];


        const btn = document.createElement('button');
        btn.className = 'btn';
        btn.id = 'show-report';
        btn.type = 'button';
        btn.textContent = 'Report';
        btn.hidden = logsBtn.hidden;
        logsBtn.after(btn);
        // Follow Show Logs: visible only when it is (i.e. editing an existing well).
        new MutationObserver(() => { btn.hidden = logsBtn.hidden; })
            .observe(logsBtn, { attributes: true, attributeFilter: ['hidden'] });

        btn.addEventListener('click', async () => {
            const well = document.getElementById('database_name').value.trim();

            try {
                const data = await api(alertsUrl(well));
                const rows = parse(Array.isArray(data) ? data : (data.alerts || []));

                const reportHtml = `
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Alerts Report - ${esc(well)}</title>

            <style>
                ${css}

                body{
                    font-family: Arial, sans-serif;
                    margin:20px;
                    background:#ffffff;
                    color:#111;
                }

                .toolbar{
                    display:flex;
                    justify-content:flex-end;
                    gap:10px;
                    margin-bottom:20px;
                    padding-bottom:12px;
                    border-bottom:1px solid #ddd;
                }

                .toolbar button{
                    background:#0b5cab;
                    color:#fff;
                    border:none;
                    padding:10px 18px;
                    border-radius:6px;
                    font-size:14px;
                    font-weight:600;
                    cursor:pointer;
                }

                .toolbar button:hover{
                    background:#084a8c;
                }

                @media print{
                    .toolbar{
                        display:none !important;
                    }
                }
            </style>
        </head>

        <body>

            <div class="toolbar">
                <button onclick="window.print()">
                    Print / Download PDF
                </button>
            </div>

            ${html(well, rows)}

        </body>
        </html>`;

                const reportWindow = window.open(
                    '',
                    '_blank',
                    'width=1400,height=900'
                );

                if (!reportWindow) {
                    alert('Popup blocked. Please allow popups for this site.');
                    return;
                }

                reportWindow.document.open();
                reportWindow.document.write(reportHtml);
                reportWindow.document.close();

            } catch (e) {
                alert(`Could not load report: ${e.message || e}`);
            }
        });

        modal.querySelector('#report-close').addEventListener('click', () => { modal.hidden = true; });
        modal.addEventListener('click', (e) => { if (e.target === modal) modal.hidden = true; });
        modal.querySelector('#report-print').addEventListener('click', () => window.print());

        // CSV button is currently commented out in the footer, so guard against null.
        const csvBtn = modal.querySelector('#report-csv');
        if (csvBtn) csvBtn.addEventListener('click', () => {
            const a = document.createElement('a');
            a.href = URL.createObjectURL(new Blob([csv(rows)], { type: 'text/csv' }));
            a.download = `${well}-alerts-report.csv`;
            a.click();
            URL.revokeObjectURL(a.href);
        });
    }

    document.addEventListener('DOMContentLoaded', init);
})();