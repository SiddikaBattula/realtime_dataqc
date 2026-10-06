/* Report button: summarises output/<well>/alerts.json for the well whose
   rules form is open. Needs shared.js first (api, escapeHtml).

   Log line format:
   "[dd-mm-yy HH-MM-SS] <message> where BD:202.61m, MD:1551.01m"
*/
(function () {
    // Full output/<well>/alerts.json, no limit or age filter (see
    // get_all_alerts in the config API). Returns {database_name, count, alerts}.
    const alertsUrl = (well) => `/alerts/all/${encodeURIComponent(well)}`;

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

    function html(well, rows) {
        if (!rows.length) return '<p class="modal-intro">No alerts have been logged for this well.</p>';
        const s = summarise(rows);
        const peak = Math.max(...s.byHour, 1);
        const peakHour = s.byHour.indexOf(peak);
        const top = s.groups[0];
        const first = rows[0].t, last = rows[rows.length - 1].t;
        return `
        <div class="rp-cards">
            <div><b>${rows.length}</b><span>Total alerts</span></div>
            <div><b>${s.groups.length}</b><span>Alert types</span></div>
            <div><b>${s.byDay.size}</b><span>Days with alerts</span></div>
        </div>
        <p class="rp-span">${esc(fmt(first))} &rarr; ${esc(fmt(last))}</p>

        <h3 class="rp-h">By alert type</h3>
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
    .rp-cards{display:flex;gap:12px;margin-bottom:8px}
    .rp-cards div{flex:1;padding:12px;border:1px solid var(--border,#444);border-radius:8px;text-align:center}
    .rp-cards b{display:block;font-size:24px}.rp-cards span,.rp-span{opacity:.7;font-size:12px}
    .rp-h{margin:18px 0 6px;font-size:14px}
    .rp-table{width:100%;border-collapse:collapse;font-size:12px}
    .rp-table th,.rp-table td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--border,#3334)}
    .rp-note{margin-top:16px;font-size:13px}
    @media print {

    /* Hide everything except report */
    body > *:not(#report-modal) {
        display: none !important;
    }

    #report-modal {
        position: static !important;
        inset: auto !important;
        width: 100% !important;
        height: auto !important;
        overflow: visible !important;
        background: white !important;
    }

    .modal-card {
        width: 100% !important;
        max-width: none !important;
        height: auto !important;
        max-height: none !important;
        overflow: visible !important;
        box-shadow: none !important;
    }

    /* THIS IS THE IMPORTANT PART */
    .modal-body {
        height: auto !important;
        max-height: none !important;
        overflow: visible !important;
    }

    .modal-foot,
    .modal-head button {
        display: none !important;
    }

    .rp-table {
        page-break-inside: auto;
    }

    .rp-table tr {
        page-break-inside: avoid;
        page-break-after: auto;
    }

    h3 {
        page-break-after: avoid;
    }

    @page {
        size: auto;
        margin: 10mm;
    }
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

        // <button class="btn" id="report-print" type="button">Print / PDF</button>
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
            well = document.getElementById('database_name').value.trim();
            document.getElementById('report-title').textContent = `Report — ${well}`;
            body.innerHTML = '<p class="modal-intro">Loading…</p>';
            modal.hidden = false;
            try {
                const data = await api(alertsUrl(well));
                rows = parse(Array.isArray(data) ? data : (data.alerts || []));
                body.innerHTML = html(well, rows);
            } catch (e) {
                rows = [];
                body.innerHTML = `<p class="modal-error">Could not load alerts: ${esc(e.message || e)}</p>`;
            }
        });

        modal.querySelector('#report-close').addEventListener('click', () => { modal.hidden = true; });
        modal.addEventListener('click', (e) => { if (e.target === modal) modal.hidden = true; });
        modal.querySelector('#report-print').addEventListener('click', () => window.print());
        modal.querySelector('#report-csv').addEventListener('click', () => {
            const a = document.createElement('a');
            a.href = URL.createObjectURL(new Blob([csv(rows)], { type: 'text/csv' }));
            a.download = `${well}-alerts-report.csv`;
            a.click();
            URL.revokeObjectURL(a.href);
        });
    }

    document.addEventListener('DOMContentLoaded', init);
})();



