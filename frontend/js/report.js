/* Report button: summarises output/<well>/alerts.json for the well whose
   rules form is open, on screen and as a downloadable PDF.

   Needs, loaded first: shared.js (api, escapeHtml, classify),
   report-data.js (parsing + summary) and report-pdf.js (the PDF file).
*/
(function () {
    // Full output/<well>/alerts.json, no limit or age filter (see
    // get_all_alerts in the config API). Returns {database_name, count, alerts}.
    const alertsUrl = (well) => `/alerts/all/${encodeURIComponent(well)}`;

    // frontend/images/, relative to the page like the page's own <img>.
    const LOGO_URL = '../images/logo-default-223x59.png';

    const { stamp, duration, formatDuration, pad } = ReportData;
    const esc = (s) => (window.escapeHtml ? escapeHtml(String(s)) : String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])));

    function details() {
        const field = (id) => {
            const el = document.getElementById(id);
            return el ? el.value.trim() : '';
        };
        // `auth` is app.js's signed-in user, when there is one.
        const user = typeof auth !== 'undefined' && auth.user ? auth.user.email : '';
        const now = new Date();
        const well = field('database_name');
        return {
            well,
            region: field('region'),
            preparedBy: user,
            generated: stamp(now),
            filename: `${well}-alerts-report-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}.pdf`,
        };
    }

    const sevClass = (sev) => `rp-sev rp-sev-${sev.toLowerCase()}`;

    // "2460.81 m" never breaks between the number and its unit.
    const unit = (text) => esc(text).replace(/(\d) (?=[^\s\d-])/g, '$1&nbsp;');

    // Date over time, so a timestamp column is narrow enough for every
    // column to fit the window without scrolling sideways.
    const when = (t) => {
        const [date, time] = stamp(t).split(' ');
        return `<span class="rp-when">${esc(date)}<small>${esc(time)}</small></span>`;
    };

    // Laid out exactly as report-pdf.js writes the file - the same sections
    // in the same order with the same columns - so what is on screen is what
    // the download will be.
    function header(info, s) {
        return `
        <div class="rp-header">
            <img class="rp-logo" src="${esc(LOGO_URL)}" alt="Logo" onerror="this.style.display='none'">
            <div class="rp-header-text">
                <p class="rp-kicker">REAL-TIME DATA QC</p>
                <h2>Alerts Summary Report</h2>
                ${info.preparedBy ? `<p class="rp-span">Prepared by ${esc(info.preparedBy)}</p>` : ''}
            </div>
        </div>
        <dl class="rp-info">
            <div><dt>Well</dt><dd>${esc(info.well)}</dd></div>
            <div><dt>Base region</dt><dd>${esc(info.region || '-')}</dd></div>
            <div><dt>Time Duration</dt><dd>${s.total ? `${esc(stamp(s.first))} to ${esc(stamp(s.last))}` : '-'}</dd></div>
        </dl>`;
    }

    function html(info, s) {
        if (!s.total) return header(info, s) + '<p class="modal-intro">No alerts have been logged for this well.</p>';

        return `
        ${header(info, s)}

        <h3 class="rp-h">Summary</h3>
        <div class="rp-cards">
            <div><b>${s.total}</b><span>Total alerts</span></div>
            <div><b>${s.groups.length}</b><span>Alert types</span></div>
            <div><b>${s.critical}</b><span>Critical alerts</span></div>
        </div>

        <h3 class="rp-h">Alerts by type</h3>
        <table class="rp-table"><thead><tr>
            <th>Severity</th><th>Alert</th><th class="rp-num">Count</th>
            <th class="rp-num">Groups of alerts</th><th class="rp-num">Time Duration</th>
        </tr></thead><tbody>
        ${s.groups.map((g) => `<tr>
            <td><span class="${sevClass(g.severity)}">${esc(g.severity)}</span></td>
            <td class="rp-wrap">${esc(g.title)}</td>
            <td class="rp-num">${g.count}</td><td class="rp-num">${g.episodes.length}</td>
            <td class="rp-num">${esc(formatDuration(g.duration))}</td>
        </tr>`).join('')}
        </tbody></table>

        <h3 class="rp-h">Timeline of alert groups</h3>
        <p class="rp-hint">Alerts of the same type less than ${ReportData.EPISODE_GAP_MINUTES} minutes apart are one group of alerts.</p>
        <table class="rp-table"><thead><tr>
            <th>First alert</th><th>Last alert</th><th>Span</th><th>Severity</th><th>Alert</th><th class="rp-num">Alerts</th><th>Bit depth</th>
        </tr></thead><tbody>
        ${s.timeline.map((e) => `<tr>
            <td>${when(e.first)}</td><td>${when(e.last)}</td><td>${esc(duration(e.duration))}</td>
            <td><span class="${sevClass(e.severity)}">${esc(e.severity)}</span></td>
            <td class="rp-wrap">${esc(e.title)}</td><td class="rp-num">${e.count}</td><td>${unit(e.bitDepth)}</td>
        </tr>`).join('')}
        </tbody></table>`;
    }


    const css = `
    /* ---------- Header (screen + print) ---------- */
    .rp-header {
        display: block;
        text-align: center;
        margin-bottom: 15px;
        padding-bottom: 10px;
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

    /* ---------- Report details ---------- */
    .rp-info{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px 16px;margin:0 0 16px}
    .rp-info div{min-width:0}
    .rp-info dt{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;opacity:.7}
    .rp-info dd{margin:2px 0 0;font-size:14px;font-weight:600;overflow-wrap:anywhere}

    /* ---------- Summary cards ---------- */
    .rp-cards{display:flex;flex-wrap:wrap;gap:5px;margin-top: 0;margin-bottom:0px}
    .rp-cards div{flex:1 1 120px;padding:10px 12px;border:1px solid var(--border,#555);border-radius:8px;text-align:center}
    .rp-cards b{display:block;font-size:28px;font-weight:700;line-height:1.2}
    .rp-cards span{font-size:13px;font-weight:600;opacity:.85}

    /* ---------- Table ---------- */
    .rp-h{margin:14px 0 6px;font-size:16px;font-weight:700}
    .rp-hint{margin:-4px 0 8px;font-size:12px;opacity:.75}
    .rp-table{width:100%;border-collapse:collapse;font-size:12.5px}
    .rp-table th{
        text-align:left;padding:7px 6px;font-weight:700;
        background:rgba(128,128,128,.18);
        border-bottom:2px solid var(--border,#666);
        vertical-align:bottom;
    }
    .rp-table td{text-align:left;padding:6px;border-bottom:1px solid var(--border,#3336);vertical-align:top;overflow-wrap:break-word}
    .rp-table td.rp-wrap{min-width:130px}
    .rp-when{white-space:nowrap}
    .rp-table td .rp-when small{margin-top:1px;font-size:12px}
    .rp-table td small{display:block;font-size:11px;opacity:.7;margin-top:2px}
    .rp-table .rp-num{text-align:right;white-space:nowrap}
    .rp-table tbody tr:nth-child(even){background:rgba(128,128,128,.07)}
    .rp-sev{font-weight:700;font-size:12px}
    .rp-sev-critical{color:#ff6b6b}
    .rp-sev-warning{color:#f0b429}
    .rp-notes{margin:0;padding-left:18px;font-size:13px;line-height:1.55}

    .rp-header-text p.rp-kicker{margin:0 0 4px;font-size:11px;font-weight:600;letter-spacing:.6px;opacity:.7}

    .rp-header-text p.rp-span {
        font-size: 13px;      /* change to taste: 12px smaller, 14px larger */
        font-weight: 400;     /* optional: lighter than the bold well line */
        margin: 4px 0;
        opacity: .8;          /* optional: slightly muted */
    }

    /* A little wider than the settings form, and never scrolled sideways:
       every column wraps to fit instead. */
    #report-modal .modal-card{max-width:1080px}
    #report-body{overflow-x:hidden;padding:16px 20px}

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

        .rp-cards div { border: 1px solid #555 !important; background: #fff !important; }
        .rp-cards b { font-size: 26px; color: #000 !important; }
        .rp-cards span { color: #333 !important; }
        .rp-span { color: #333 !important; }
        .rp-sev-critical { color: #c01c28 !important; }
        .rp-sev-warning { color: #be7800 !important; }

        .rp-h { color: #000 !important; page-break-after: avoid; }

        .rp-table { font-size: 11px; page-break-inside: auto; }
        .rp-table th {
            background: #e9ecef !important;
            border-bottom: 2px solid #111 !important;
            color: #000 !important;
        }
        .rp-table td { border-bottom: 1px solid #999 !important; white-space: normal; }
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
                <p class="modal-error" id="report-error" hidden></p>
                <button class="btn btn-primary" id="report-pdf" type="button" disabled>Download PDF</button>
            </footer>
        </div>`;
        document.body.appendChild(modal);

        const body = modal.querySelector('#report-body');
        const pdfBtn = modal.querySelector('#report-pdf');
        const errorEl = modal.querySelector('#report-error');
        let info = null, summary = null;

        const showError = (text) => {
            errorEl.textContent = text || '';
            errorEl.hidden = !text;
        };

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

            body.innerHTML = '<p class="modal-intro">Loading…</p>';
            modal.hidden = false;
            pdfBtn.disabled = true;
            showError('');

            try {
                // Alerts and the well record (which holds the region) in parallel.
                const [data, wellRecord] = await Promise.all([
                    api(alertsUrl(well)),
                    api(`/wells/${encodeURIComponent(well)}`),
                ]);

                const region = (wellRecord.region || '').trim();

                document.getElementById('report-title').textContent =
                    `Report — ${well}${region ? ` (${region})` : ''}`;

                const rows = ReportData.parse(
                    Array.isArray(data) ? data : (data.alerts || [])
                );

                info = details();
                info.region = region;          // overrides the form value
                summary = ReportData.summarise(rows);

                body.innerHTML = html(info, summary);
                pdfBtn.disabled = false;
            } catch (e) {
                summary = null;
                body.innerHTML = `<p class="modal-error">Could not load alerts: ${esc(e.message || e)}</p>`;
            }
        });

        modal.querySelector('#report-close').addEventListener('click', () => { modal.hidden = true; });
        modal.addEventListener('click', (e) => { if (e.target === modal) modal.hidden = true; });


        pdfBtn.addEventListener('click', async () => {
            if (!summary) return;
            pdfBtn.disabled = true;
            pdfBtn.textContent = 'Preparing PDF…';
            showError('');
            try {
                await ReportPdf.download(info, summary, LOGO_URL);
            } catch (e) {
                showError(`Could not build the PDF: ${e.message || e}. Use Print and choose "Save as PDF" instead.`);
            } finally {
                pdfBtn.disabled = false;
                pdfBtn.textContent = 'Download PDF';
            }
        });
    }

    document.addEventListener('DOMContentLoaded', init);
})();
