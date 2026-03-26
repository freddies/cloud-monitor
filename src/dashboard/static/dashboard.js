/**
 * AI Cloud Monitor – Dashboard JavaScript
 * Real-time dashboard with auto-refresh and interactive charts.
 */

const REFRESH_INTERVAL = 5000; // 5 seconds
const API_BASE = '';

// ── State ────────────────────────────────────────────────
let state = {
    summary: null,
    metricSeries: [],
    selectedHost: '',
    selectedMetric: '',
    refreshTimer: null,
    isLoading: false,
};

// ── Initialization ───────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initDashboard();
});

async function initDashboard() {
    console.log('🔍 AI Cloud Monitor Dashboard initializing...');

    // Load initial data
    await refreshAll();

    // Load metric series for selectors
    await loadMetricSeries();

    // Set up event listeners
    document.getElementById('load-chart-btn').addEventListener('click', loadSelectedChart);

    // Start auto-refresh
    state.refreshTimer = setInterval(refreshAll, REFRESH_INTERVAL);

    console.log('✅ Dashboard initialized');
}

// ── Data Fetching ────────────────────────────────────────
async function fetchAPI(endpoint) {
    try {
        const response = await fetch(`${API_BASE}${endpoint}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
    } catch (error) {
        console.error(`API error (${endpoint}):`, error);
        return null;
    }
}

async function refreshAll() {
    if (state.isLoading) return;
    state.isLoading = true;

    try {
        const [summary, health] = await Promise.all([
            fetchAPI('/api/dashboard/summary'),
            fetchAPI('/api/health'),
        ]);

        if (summary) {
            state.summary = summary;
            updateStatsCards(summary);
            updateSeverityChart(summary.severity_counts);
            updateAnomalyTimeline(summary.recent_anomalies);
            updateAnomalyList(summary.recent_anomalies);
            updateAlertList(summary.recent_alerts);
        }

        if (health) {
            updateSystemStatus(health);
            updateServiceHealth(health.services);
        }

        updateLastRefresh();
    } catch (error) {
        console.error('Refresh error:', error);
    } finally {
        state.isLoading = false;
    }
}

async function loadMetricSeries() {
    const data = await fetchAPI('/api/metrics/series');
    if (!data) return;

    state.metricSeries = data.series;

    // Populate host selector
    const hosts = [...new Set(data.series.map(s => s.host))].sort();
    const hostSelect = document.getElementById('host-select');
    hostSelect.innerHTML = '<option value="">Select Host</option>';
    hosts.forEach(host => {
        const opt = document.createElement('option');
        opt.value = host;
        opt.textContent = host;
        hostSelect.appendChild(opt);
    });

    // Update metric selector when host changes
    hostSelect.addEventListener('change', () => {
        const selectedHost = hostSelect.value;
        const metrics = [...new Set(
            data.series
                .filter(s => s.host === selectedHost)
                .map(s => s.metric_type)
        )].sort();

        const metricSelect = document.getElementById('metric-select');
        metricSelect.innerHTML = '<option value="">Select Metric</option>';
        metrics.forEach(mt => {
            const opt = document.createElement('option');
            opt.value = mt;
            opt.textContent = mt.replace(/_/g, ' ').toUpperCase();
            metricSelect.appendChild(opt);
        });
    });

    // Auto-select first host/metric if available
    if (hosts.length > 0) {
        hostSelect.value = hosts[0];
        hostSelect.dispatchEvent(new Event('change'));
        setTimeout(() => {
            const metricSelect = document.getElementById('metric-select');
            if (metricSelect.options.length > 1) {
                metricSelect.selectedIndex = 1;
                loadSelectedChart();
            }
        }, 100);
    }
}

async function loadSelectedChart() {
    const host = document.getElementById('host-select').value;
    const metric = document.getElementById('metric-select').value;

    if (!host || !metric) {
        console.warn('Please select both host and metric');
        return;
    }

    state.selectedHost = host;
    state.selectedMetric = metric;

    const data = await fetchAPI(`/api/metrics/history/${host}/${metric}?minutes=60`);
    if (!data || !data.data.length) {
        renderEmptyChart('metrics-chart', 'No data available for selection');
        return;
    }

    renderMetricsChart(data.data, host, metric);
}

// ── UI Updates ───────────────────────────────────────────
function updateStatsCards(summary) {
    document.getElementById('stat-metrics').textContent =
        summary.stats.total_metric_series || 0;
    document.getElementById('stat-anomalies').textContent =
        summary.stats.anomaly_history_count || 0;
    document.getElementById('stat-alerts').textContent =
        summary.stats.alert_history_count || 0;

    const serviceCount = Object.keys(summary.services || {}).length;
    document.getElementById('stat-services').textContent = serviceCount;
}

function updateSystemStatus(health) {
    const statusEl = document.getElementById('system-status');
    const dot = statusEl.querySelector('.status-dot');
    const text = statusEl.querySelector('.status-text');

    dot.className = `status-dot ${health.status}`;
    text.textContent = health.status.charAt(0).toUpperCase() + health.status.slice(1);
}

function updateLastRefresh() {
    const el = document.getElementById('last-updated');
    const now = new Date();
    el.textContent = `Updated: ${now.toLocaleTimeString()}`;
}

function updateServiceHealth(services) {
    const container = document.getElementById('service-health-list');

    if (!services || Object.keys(services).length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="emoji">🔌</div>
                <div>No services reporting</div>
            </div>`;
        return;
    }

    let html = '';
    for (const [name, data] of Object.entries(services)) {
        const status = data.status || 'unknown';
        const uptime = data.uptime_seconds
            ? formatUptime(data.uptime_seconds)
            : '--';

        html += `
            <div class="service-item">
                <div class="service-info">
                    <div class="service-dot ${status}"></div>
                    <div>
                        <div class="service-name">${name}</div>
                        <div class="service-uptime">Uptime: ${uptime}</div>
                    </div>
                </div>
                <span class="status-badge ${status}">${status}</span>
            </div>`;
    }

    container.innerHTML = html;
}

function updateAlertList(alerts) {
    const container = document.getElementById('alert-list');

    if (!alerts || alerts.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="emoji">✅</div>
                <div>No recent alerts</div>
            </div>`;
        return;
    }

    let html = '';
    for (const alert of alerts) {
        const severity = alert.severity || 'low';
        const status = alert.status || 'firing';
        const time = formatTimestamp(alert.timestamp);

        html += `
            <div class="alert-item severity-${severity}">
                <div class="alert-title">${escapeHtml(alert.title || 'Alert')}</div>
                <div class="alert-meta">
                    <span class="severity-badge ${severity}">${severity}</span>
                    <span class="status-badge ${status}">${status}</span>
                    <span>🖥️ ${escapeHtml(alert.host || '--')}</span>
                    <span>⏱️ ${time}</span>
                </div>
                <div class="alert-actions">
                    <button class="btn btn-sm btn-warning"
                            onclick="acknowledgeAlert('${alert.alert_id}')">
                        Acknowledge
                    </button>
                    <button class="btn btn-sm btn-success"
                            onclick="resolveAlert('${alert.alert_id}')">
                        Resolve
                    </button>
                </div>
            </div>`;
    }

    container.innerHTML = html;
}

function updateAnomalyList(anomalies) {
    const container = document.getElementById('anomaly-list');

    if (!anomalies || anomalies.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="emoji">🎉</div>
                <div>No recent anomalies</div>
            </div>`;
        return;
    }

    let html = '';
    for (const anomaly of anomalies) {
        const severity = anomaly.severity || 'low';
        const time = formatTimestamp(anomaly.timestamp);
        const score = anomaly.anomaly_score
            ? (anomaly.anomaly_score * 100).toFixed(1)
            : '--';

        html += `
            <div class="anomaly-item severity-${severity}">
                <div class="anomaly-title">
                    ${escapeHtml(anomaly.metric_type || '--')} on ${escapeHtml(anomaly.host || '--')}
                </div>
                <div class="anomaly-meta">
                    <span class="severity-badge ${severity}">${severity}</span>
                    <span>📈 ${anomaly.value?.toFixed(2) || '--'}</span>
                    <span>🎯 ${score}%</span>
                    <span>⏱️ ${time}</span>
                </div>
            </div>`;
    }

    container.innerHTML = html;
}

// ── Charts (Plotly) ──────────────────────────────────────
function updateSeverityChart(counts) {
    const container = document.getElementById('severity-chart');

    if (!counts || Object.keys(counts).length === 0) {
        renderEmptyChart('severity-chart', 'No anomaly data yet');
        return;
    }

    const labels = Object.keys(counts);
    const values = Object.values(counts);

    const colorMap = {
        low: '#6366f1',
        medium: '#f59e0b',
        high: '#ef4444',
        critical: '#dc2626',
    };

    const colors = labels.map(l => colorMap[l] || '#8b8fa3');

    const data = [{
        type: 'pie',
        labels: labels.map(l => l.toUpperCase()),
        values: values,
        marker: { colors: colors },
        hole: 0.5,
        textinfo: 'label+value',
        textfont: { color: '#e4e6f0', size: 12 },
    }];

    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { color: '#8b8fa3' },
        showlegend: true,
        legend: {
            font: { color: '#8b8fa3', size: 11 },
            orientation: 'h',
            y: -0.1,
        },
        margin: { t: 10, b: 30, l: 10, r: 10 },
    };

    Plotly.newPlot(container, data, layout, {
        responsive: true,
        displayModeBar: false,
    });
}

function renderMetricsChart(dataPoints, host, metricType) {
    const container = document.getElementById('metrics-chart');

    const timestamps = dataPoints.map(d => d.timestamp || new Date().toISOString());
    const values = dataPoints.map(d => d.value);

    // Compute rolling mean and std for anomaly band
    const windowSize = 10;
    const rollingMean = [];
    const upperBand = [];
    const lowerBand = [];

    for (let i = 0; i < values.length; i++) {
        const start = Math.max(0, i - windowSize + 1);
        const window = values.slice(start, i + 1);
        const mean = window.reduce((a, b) => a + b, 0) / window.length;
        const std = Math.sqrt(
            window.reduce((sum, v) => sum + (v - mean) ** 2, 0) / window.length
        );
        rollingMean.push(mean);
        upperBand.push(mean + 2 * std);
        lowerBand.push(mean - 2 * std);
    }

    const traces = [
        {
            x: timestamps,
            y: upperBand,
            type: 'scatter',
            mode: 'lines',
            line: { width: 0 },
            showlegend: false,
            hoverinfo: 'skip',
        },
        {
            x: timestamps,
            y: lowerBand,
            type: 'scatter',
            mode: 'lines',
            line: { width: 0 },
            fill: 'tonexty',
            fillcolor: 'rgba(99, 102, 241, 0.1)',
            showlegend: false,
            hoverinfo: 'skip',
        },
        {
            x: timestamps,
            y: rollingMean,
            type: 'scatter',
            mode: 'lines',
            name: 'Rolling Mean',
            line: { color: '#6366f1', width: 1, dash: 'dash' },
        },
        {
            x: timestamps,
            y: values,
            type: 'scatter',
            mode: 'lines',
            name: metricType.replace(/_/g, ' ').toUpperCase(),
            line: { color: '#22c55e', width: 2 },
        },
    ];

    // Highlight anomalous points
    const anomalyX = [];
    const anomalyY = [];
    for (let i = 0; i < values.length; i++) {
        if (values[i] > upperBand[i] || values[i] < lowerBand[i]) {
            anomalyX.push(timestamps[i]);
            anomalyY.push(values[i]);
        }
    }

    if (anomalyX.length > 0) {
        traces.push({
            x: anomalyX,
            y: anomalyY,
            type: 'scatter',
            mode: 'markers',
            name: 'Anomaly',
            marker: { color: '#ef4444', size: 8, symbol: 'x' },
        });
    }

    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { color: '#8b8fa3' },
        title: {
            text: `${host} — ${metricType.replace(/_/g, ' ')}`,
            font: { size: 14, color: '#e4e6f0' },
        },
        xaxis: {
            gridcolor: '#2a2e42',
            linecolor: '#2a2e42',
            tickfont: { size: 10 },
        },
        yaxis: {
            gridcolor: '#2a2e42',
            linecolor: '#2a2e42',
            tickfont: { size: 10 },
        },
        legend: {
            font: { color: '#8b8fa3', size: 10 },
            orientation: 'h',
            y: -0.2,
        },
        margin: { t: 40, b: 50, l: 50, r: 20 },
    };

    Plotly.newPlot(container, traces, layout, {
        responsive: true,
        displayModeBar: false,
    });
}

function updateAnomalyTimeline(anomalies) {
    const container = document.getElementById('anomaly-timeline');

    if (!anomalies || anomalies.length === 0) {
        renderEmptyChart('anomaly-timeline', 'No anomaly data yet');
        return;
    }

    const colorMap = {
        low: '#6366f1',
        medium: '#f59e0b',
        high: '#ef4444',
        critical: '#dc2626',
    };

    const timestamps = anomalies.map(a => a.timestamp);
    const scores = anomalies.map(a => (a.anomaly_score || 0) * 100);
    const colors = anomalies.map(a => colorMap[a.severity] || '#8b8fa3');
    const texts = anomalies.map(a =>
        `${a.host}<br>${a.metric_type}<br>Score: ${((a.anomaly_score || 0) * 100).toFixed(1)}%`
    );

    const data = [{
        x: timestamps,
        y: scores,
        type: 'scatter',
        mode: 'markers',
        marker: {
            color: colors,
            size: scores.map(s => Math.max(6, s / 8)),
            opacity: 0.8,
        },
        text: texts,
        hoverinfo: 'text',
    }];

    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { color: '#8b8fa3' },
        xaxis: {
            gridcolor: '#2a2e42',
            linecolor: '#2a2e42',
            tickfont: { size: 10 },
            title: { text: 'Time', font: { size: 11 } },
        },
        yaxis: {
            gridcolor: '#2a2e42',
            linecolor: '#2a2e42',
            tickfont: { size: 10 },
            title: { text: 'Anomaly Score (%)', font: { size: 11 } },
            range: [0, 105],
        },
        margin: { t: 10, b: 50, l: 50, r: 20 },
        shapes: [{
            type: 'line',
            y0: 85, y1: 85,
            x0: 0, x1: 1,
            xref: 'paper',
            line: { color: '#ef4444', width: 1, dash: 'dash' },
        }],
    };

    Plotly.newPlot(container, data, layout, {
        responsive: true,
        displayModeBar: false,
    });
}

function renderEmptyChart(containerId, message) {
    const container = document.getElementById(containerId);
    container.innerHTML = `
        <div class="empty-state" style="height:100%;display:flex;align-items:center;justify-content:center;">
            <div>
                <div class="emoji">📭</div>
                <div>${message}</div>
            </div>
        </div>`;
}

// ── Alert Actions ────────────────────────────────────────
async function acknowledgeAlert(alertId) {
    const result = await fetch(`/api/alerts/${alertId}/acknowledge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user: 'dashboard_user' }),
    });

    if (result.ok) {
        console.log(`Alert ${alertId} acknowledged`);
        await refreshAll();
    }
}

async function resolveAlert(alertId) {
    const result = await fetch(`/api/alerts/${alertId}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    });

    if (result.ok) {
        console.log(`Alert ${alertId} resolved`);
        await refreshAll();
    }
}

// ── Utilities ────────────────────────────────────────────
function formatTimestamp(ts) {
    if (!ts) return '--';
    try {
        const d = new Date(ts);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
        return ts;
    }
}

function formatUptime(seconds) {
    if (!seconds) return '--';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}