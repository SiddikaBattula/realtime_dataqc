/*
  Writes the alerts report as a real PDF file and downloads it.

  Built with jsPDF + jspdf-autotable, kept in frontend/vendor/ rather than
  fetched from a CDN, so it works on a rig-side network with no internet.
  They are only loaded the first time someone asks for a PDF; the dashboard
  itself never pays for them.

  Reads the summary ReportData.summarise() made (report-data.js), the same
  one report.js shows on screen, so the file and the screen cannot disagree.
*/
'use strict';

const ReportPdf = (function () {
    const LIBS = ['vendor/jspdf.umd.min.js', 'vendor/jspdf.plugin.autotable.min.js'];

    // Taken from the logo: its charcoal and its yellow drop.
    const INK = [56, 56, 56];
    const ACCENT = [255, 235, 20];
    const MUTED = [110, 110, 110];
    const STRIPE = [245, 245, 245];
    const SEVERITY_COLOUR = { Critical: [192, 28, 40], Warning: [190, 120, 0] };

    const MARGIN = 14;

    let loading = null;

    function loadScript(src) {
        return new Promise((resolve, reject) => {
            const s = document.createElement('script');
            s.src = src;
            s.onload = resolve;
            s.onerror = () => reject(new Error(`could not load ${src}`));
            document.head.appendChild(s);
        });
    }

    function loadLibs() {
        if (window.jspdf && window.jspdf.jsPDF && window.jspdf.jsPDF.API.autoTable) {
            return Promise.resolve();
        }
        // In order: the table plugin attaches itself to jsPDF as it loads.
        loading = loading || LIBS.reduce((p, src) => p.then(() => loadScript(src)), Promise.resolve());
        return loading;
    }

    // The logo as a data URL plus its real proportions - it is taller than it
    // is wide, whatever its file name says, so it is never stretched.
    function loadLogo(url) {
        return new Promise((resolve) => {
            const img = new Image();
            img.onload = () => {
                try {
                    // Drawn at most 300px tall: plenty for 22mm on paper, and
                    // the full-size file would make every PDF several MB.
                    const scale = Math.min(1, 300 / img.naturalHeight);
                    const c = document.createElement('canvas');
                    c.width = Math.round(img.naturalWidth * scale);
                    c.height = Math.round(img.naturalHeight * scale);
                    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
                    resolve({ data: c.toDataURL('image/png'), w: c.width, h: c.height });
                } catch (e) {
                    resolve(null);
                }
            };
            img.onerror = () => resolve(null);
            img.src = url;
        });
    }

    // jsPDF's built-in fonts cover Latin-1 only; anything else prints as junk.
    const clean = (s) => String(s)
        .replace(/[–—]/g, '-')
        .replace(/→/g, 'to')
        .replace(/[^\x09\x0A\x0D\x20-\x7E\xA0-\xFF]/g, '?');

    function header(doc, info, logo) {
        const width = doc.internal.pageSize.getWidth();
        const top = 12;
        const logoH = 22;
        // Title block centred on the page; the logo sits on the left of it.
        const centre = { align: 'center' };
        const textX = width / 2;

        if (logo) {
            const logoW = logoH * (logo.w / logo.h);
            doc.addImage(logo.data, 'PNG', MARGIN, top, logoW, logoH, undefined, 'FAST');
        }

        doc.setTextColor(...MUTED);
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(9);
        doc.text('REAL-TIME DATA QC', textX, top + 6, centre);

        doc.setTextColor(...INK);
        doc.setFont('helvetica', 'bold');
        doc.setFontSize(18);
        doc.text('Alerts Report', textX, top + 14, centre);

        doc.setFont('helvetica', 'normal');
        doc.setFontSize(11);
        doc.text(clean(`Well: ${info.well}`), textX, top + 20.5, centre);

        if (info.preparedBy) {
            doc.setFontSize(8.5);
            doc.setTextColor(...MUTED);
            doc.text(clean(`Prepared by ${info.preparedBy}`), width - MARGIN, top + 6, { align: 'right' });
        }

        const y = top + logoH + 4;
        doc.setFillColor(...ACCENT);
        doc.rect(MARGIN, y, width - 2 * MARGIN, 1.6, 'F');
        doc.setFillColor(...INK);
        doc.rect(MARGIN, y + 1.6, width - 2 * MARGIN, 0.5, 'F');

        return y + 8;
    }

    function heading(doc, text, y) {
        // Never leave a heading alone at the bottom of a page.
        if (y > doc.internal.pageSize.getHeight() - 40) {
            doc.addPage();
            y = 18;
        }
        doc.setTextColor(...INK);
        doc.setFont('helvetica', 'bold');
        doc.setFontSize(12);
        doc.text(text, MARGIN, y);
        doc.setFillColor(...ACCENT);
        doc.rect(MARGIN, y + 1.5, 18, 0.9, 'F');
        return y + 5;
    }

    function cards(doc, items, y) {
        const width = doc.internal.pageSize.getWidth() - 2 * MARGIN;
        const gap = 4;
        const w = (width - gap * (items.length - 1)) / items.length;
        const h = 18;

        items.forEach(([value, label], i) => {
            const x = MARGIN + i * (w + gap);
            doc.setDrawColor(200, 200, 200);
            doc.setFillColor(250, 250, 250);
            doc.roundedRect(x, y, w, h, 2, 2, 'FD');
            doc.setFillColor(...INK);
            doc.rect(x, y, 1.4, h, 'F');

            doc.setTextColor(...INK);
            doc.setFont('helvetica', 'bold');
            doc.setFontSize(16);
            doc.text(String(value), x + w / 2, y + 9, { align: 'center' });

            doc.setFont('helvetica', 'normal');
            doc.setFontSize(8);
            doc.setTextColor(...MUTED);
            doc.text(label, x + w / 2, y + 14.5, { align: 'center' });
        });

        return y + h + 8;
    }

    function table(doc, y, head, body, options = {}) {
        doc.autoTable({
            startY: y,
            margin: { left: MARGIN, right: MARGIN, top: 18, bottom: 18 },
            head: [head],
            body: body.map((row) => row.map((cell) => clean(cell))),
            theme: 'grid',
            styles: {
                font: 'helvetica', fontSize: 8, cellPadding: 1.8,
                textColor: INK, lineColor: [215, 215, 215], lineWidth: 0.2,
                overflow: 'linebreak', valign: 'middle',
            },
            headStyles: { fillColor: INK, textColor: [255, 255, 255], fontStyle: 'bold' },
            alternateRowStyles: { fillColor: STRIPE },
            // A row is never cut in half across a page break.
            rowPageBreak: 'avoid',
            ...options,
        });
        return doc.lastAutoTable.finalY + 8;
    }

    // Severity column in its colour, so the eye finds the critical rows.
    function colourSeverity(column) {
        return (data) => {
            if (data.section !== 'body' || data.column.index !== column) return;
            const colour = SEVERITY_COLOUR[data.cell.raw];
            if (colour) {
                data.cell.styles.textColor = colour;
                data.cell.styles.fontStyle = 'bold';
            }
        };
    }

    function footer(doc, info) {
        const pages = doc.getNumberOfPages();
        const width = doc.internal.pageSize.getWidth();
        const height = doc.internal.pageSize.getHeight();

        for (let i = 1; i <= pages; i++) {
            doc.setPage(i);
            doc.setDrawColor(200, 200, 200);
            doc.setLineWidth(0.2);
            doc.line(MARGIN, height - 12, width - MARGIN, height - 12);

            doc.setFont('helvetica', 'normal');
            doc.setFontSize(7.5);
            doc.setTextColor(...MUTED);
            doc.text(clean(`Real-Time Data QC  |  ${info.well}  |  Generated ${info.generated}  |  Confidential`),
                MARGIN, height - 7.5);
            doc.text(`Page ${i} of ${pages}`, width - MARGIN, height - 7.5, { align: 'right' });
        }
    }

    async function download(info, summary, logoUrl) {
        await loadLibs();
        const logo = await loadLogo(logoUrl);

        const { jsPDF } = window.jspdf;
        const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
        const { stamp, duration } = ReportData;
        const s = summary;

        let y = header(doc, info, logo);

        // ---- Report details ------------------------------------------
        y = table(doc, y - 2, ['Report details', ''], [
            ['Well', info.well],
            ['Base region', info.region || '-'],
            ['Period covered', s.total ? `${stamp(s.first)}  to  ${stamp(s.last)}` : '-'],
            ['Bit depth range', s.bitDepthRange],
            ['Total Depth', s.holeDepthRange],
        ], {
            theme: 'plain',
            showHead: 'never',
            styles: { fontSize: 9, cellPadding: 1.4, textColor: INK },
            columnStyles: { 0: { fontStyle: 'bold', cellWidth: 40, textColor: MUTED } },
        });

        if (!s.total) {
            doc.setFontSize(11);
            doc.setTextColor(...INK);
            doc.text('No alerts have been logged for this well.', MARGIN, y);
            footer(doc, info);
            doc.save(info.filename);
            return;
        }

        // ---- Summary ---------------------------------------------------
        y = heading(doc, 'Summary', y);
        y = cards(doc, [
            [s.total, 'Total alerts'],
            [s.groups.length, 'Alert types'],
            [s.critical, 'Critical alerts'],
        ], y);

        // ---- By alert --------------------------------------------------
        y = heading(doc, 'Alerts by type', y);
        y = table(doc, y,
            ['Severity', 'Alert', 'Count', 'Groups of alerts', 'First alert', 'Last alert', 'Readings', 'Bit depth'],
            s.groups.map((g) => [
                g.severity, g.title === g.category ? g.title : `${g.title}\n${g.category}`,
                String(g.count), String(g.episodes.length),
                stamp(g.first), stamp(g.last), g.reading, g.bitDepth,
            ]),
            {
                columnStyles: {
                    0: { cellWidth: 17 }, 1: { cellWidth: 38 },
                    2: { halign: 'right', cellWidth: 13 }, 3: { halign: 'right', cellWidth: 17 },
                    4: { cellWidth: 21 }, 5: { cellWidth: 21 }, 7: { cellWidth: 25 },
                },
                didParseCell: colourSeverity(0),
            });

        // ---- Timeline ----------------------------------------------------
        y = heading(doc, 'Timeline of alert groups', y);
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(8);
        doc.setTextColor(...MUTED);
        doc.text(clean(`Alerts of the same type less than ${ReportData.EPISODE_GAP_MINUTES} minutes apart are one group of alerts.`),
            MARGIN, y);
        y = table(doc, y + 3,
            ['First alert', 'Last alert', 'Span', 'Severity', 'Alert', 'Alerts', 'Bit depth'],
            s.timeline.map((e) => [
                stamp(e.first), stamp(e.last), duration(e.last - e.first),
                e.severity, e.title, String(e.count), e.bitDepth,
            ]),
            {
                columnStyles: {
                    0: { cellWidth: 26 }, 1: { cellWidth: 26 }, 2: { cellWidth: 16 },
                    3: { cellWidth: 17 }, 5: { halign: 'right', cellWidth: 13 }, 6: { cellWidth: 32 },
                },
                didParseCell: colourSeverity(3),
            });

        // ---- Notes --------------------------------------------------------
        y = heading(doc, 'Notes', y);
        const notes = info.notes.map((n) => `-  ${n}`);
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(8.5);
        doc.setTextColor(...INK);
        const width = doc.internal.pageSize.getWidth() - 2 * MARGIN;
        for (const note of notes) {
            const lines = doc.splitTextToSize(clean(note), width);
            if (y + lines.length * 4 > doc.internal.pageSize.getHeight() - 18) {
                doc.addPage();
                y = 18;
            }
            doc.text(lines, MARGIN, y);
            y += lines.length * 4 + 1.5;
        }

        footer(doc, info);
        doc.save(info.filename);
    }

    return { download };
})();
