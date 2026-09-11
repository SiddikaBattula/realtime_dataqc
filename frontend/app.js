const API_BASE_URL = 'http://localhost:8000';

document.addEventListener('DOMContentLoaded', () => {
    fetchWells();

    // Modal toggle listeners
    const modal = document.getElementById('settings-modal');
    const openBtn = document.getElementById('open-settings-btn');
    const closeBtn = document.getElementById('close-settings-btn');

    openBtn?.addEventListener('click', () => {
        if (modal) modal.style.display = 'flex';
    });

    closeBtn?.addEventListener('click', () => {
        if (modal) modal.style.display = 'none';
    });

    window.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.style.display = 'none';
        }
    });

    document.getElementById('add-well-form')?.addEventListener('submit', handleAddWell);
    setInterval(fetchWells, 2000);
});

async function fetchWells() {
    try {
        const response = await fetch(`${API_BASE_URL}/wells`);
        if (!response.ok) throw new Error('Network response error');

        const data = await response.json();
        await updateDashboardUI(data.wells);
    } catch (error) {
        console.error('Error fetching wells:', error);
    }
}

function sanitizeId(str) {
    return str.replace(/[^a-zA-Z0-9_-]/g, '_');
}

async function updateDashboardUI(wellsObj) {
    const grid = document.getElementById('wells-grid');
    const countLabel = document.getElementById('well-count');
    const wellKeys = Object.keys(wellsObj || {});

    if (countLabel) countLabel.textContent = wellKeys.length;

    if (wellKeys.length === 0) {
        grid.innerHTML = `
            <div style="grid-column: 1 / -1; text-align: center; color: #6b7280; padding: 2rem;">
                <p>No active wells monitored.</p>
            </div>`;
        return;
    }

    for (const key of wellKeys) {
        const well = wellsObj[key];
        const safeId = sanitizeId(well.database_name);
        let card = document.getElementById(`card-${safeId}`);

        if (!card) {
            card = document.createElement('div');
            card.id = `card-${safeId}`;
            card.className = 'well-card';
            card.innerHTML = `
                <div class="well-card-header">
                    <div class="well-info">
                        <h4><i class="fa-solid fa-oil-well"></i> ${well.database_name}</h4>
                    </div>
                    <button class="btn-danger-icon" onclick="deleteWell('${well.database_name}')" title="Delete Well">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </div>
                <div class="alerts-container" id="alerts-${safeId}"></div>
            `;
            grid.appendChild(card);
        }

        const rawAlerts = await fetchWellAlerts(well.database_name);
        const container = card.querySelector(`#alerts-${safeId}`);

        if (container) {
            const wasAtBottom = container.scrollHeight - container.clientHeight <= container.scrollTop + 30;
            container.innerHTML = renderAlertItems(rawAlerts);

            if (wasAtBottom || container.scrollTop === 0) {
                container.scrollTop = container.scrollHeight;
            }
        }
    }
}

async function fetchWellAlerts(databaseName) {
    try {
        const res = await fetch(`${API_BASE_URL}/alerts/${encodeURIComponent(databaseName)}`);
        if (!res.ok) return [];
        const data = await res.json();
        return data.alerts || [];
    } catch (err) {
        return [];
    }
}

function renderAlertItems(alerts) {
    if (!alerts || alerts.length === 0) {
        return `<div class="empty-alerts">[System] No alerts recorded yet.</div>`;
    }

    const processedItems = [];

    alerts.forEach(raw => {
        if (typeof raw === 'string') {
            const lines = raw.split('\n').map(l => l.trim()).filter(l => l.length > 0);
            let currentTimestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

            lines.forEach(line => {
                const timeMatch = line.match(/\[\d{2}-\d{2}-\d{2}\s+(\d{2}:\d{2}:\d{2})\]/);
                if (timeMatch) {
                    currentTimestamp = timeMatch[1];
                }

                if (line.startsWith('Alerts') || line.startsWith('Activity') || line.startsWith('Depth') || line.startsWith('TA') || line.startsWith('TG') || line.includes('change :')) {
                    return;
                }

                const cleanText = line.replace(/\[.*?\]\s*/, '').replace(/^\d+\.\s*/, '');
                if (!cleanText) return;

                let severity = "warning";
                const lower = cleanText.toLowerCase();

                if (lower.includes('below minimum') || lower.includes('above maximum') || lower.includes('limit')) {
                    severity = 'critical';
                }

                processedItems.push({
                    message: cleanText,
                    severity,
                    time: currentTimestamp
                });
            });
        } else if (typeof raw === 'object') {
            processedItems.push({
                message: raw.message || raw.detail || JSON.stringify(raw),
                severity: raw.severity || 'warning',
                time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
            });
        }
    });

    if (processedItems.length === 0) {
        return `<div class="empty-alerts">[System] All parameters normal.</div>`;
    }

    return processedItems.map(item => `
        <div class="log-entry ${item.severity}">
            <span class="log-time">[${item.time}]</span>
            <span class="log-text">${item.message}</span>
        </div>
    `).join('');
}

async function handleAddWell(e) {
    e.preventDefault();
    const database_name = document.getElementById('dbName').value.trim();
    const ip_address = document.getElementById('ipAddress').value.trim();

    if (!database_name || !ip_address) return;

    try {
        const response = await fetch(`${API_BASE_URL}/wells`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ database_name, ip_address })
        });

        if (response.ok) {
            document.getElementById('dbName').value = '';
            document.getElementById('ipAddress').value = '';
            document.getElementById('settings-modal').style.display = 'none';
            fetchWells();
        }
    } catch (error) {
        console.error('Error adding well:', error);
    }
}

async function deleteWell(database_name) {
    if (!confirm(`Delete ${database_name}?`)) return;

    try {
        await fetch(`${API_BASE_URL}/wells/${encodeURIComponent(database_name)}`, { method: 'DELETE' });
        const safeId = sanitizeId(database_name);
        document.getElementById(`card-${safeId}`)?.remove();
        fetchWells();
    } catch (error) {
        console.error('Error removing well:', error);
    }
}