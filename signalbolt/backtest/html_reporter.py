"""
HTML Report Generator with Plotly.

Creates interactive HTML reports with:
- Equity curves
- Trade distribution charts
- Regime breakdowns
- Multi-config comparison views
- ENHANCED Trade Analysis Modal:
  - Indicator snapshots (Entry vs Exit)
  - Trade journey chart (candlestick-style visualization)
  - Detailed entry analysis (WHY WE ENTERED)
  - Detailed exit analysis (WHY WE EXITED)
  - Root cause breakdown
  - Actionable lessons
- Pagination for trade history
- Auto-open option

Available for ALL users (not PRO-only).
"""

import json
import webbrowser
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Union, TYPE_CHECKING

from signalbolt.backtest.engine import BacktestResult, BacktestTrade
from signalbolt.utils.logger import get_logger

if TYPE_CHECKING:
    from signalbolt.core.indicators import IndicatorValues

log = get_logger("signalbolt.backtest.html_reporter")

# Check for plotly
try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False


# =============================================================================
# CSS STYLES (ENHANCED WITH MODAL IMPROVEMENTS)
# =============================================================================

CUSTOM_CSS = """
:root {
    --bg-primary: #0d1117;
    --bg-secondary: #161b22;
    --bg-tertiary: #21262d;
    --text-primary: #e6edf3;
    --text-secondary: #8b949e;
    --text-muted: #6e7681;
    --accent-green: #3fb950;
    --accent-red: #f85149;
    --accent-blue: #58a6ff;
    --accent-yellow: #d29922;
    --accent-purple: #a371f7;
    --accent-cyan: #39c5cf;
    --border-color: #30363d;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.6;
    padding: 2rem;
}

.container { max-width: 1400px; margin: 0 auto; }

.header {
    text-align: center;
    margin-bottom: 2rem;
    padding-bottom: 1.5rem;
    border-bottom: 1px solid var(--border-color);
}

.header h1 { font-size: 2rem; font-weight: 600; margin-bottom: 0.5rem; }
.header .subtitle { color: var(--text-secondary); font-size: 1rem; }
.header .period { color: var(--accent-blue); font-size: 0.9rem; margin-top: 0.5rem; }

.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem;
    margin-bottom: 2rem;
}

.stat-card {
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 1.25rem;
    transition: transform 0.2s, box-shadow 0.2s;
}

.stat-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.stat-card .label {
    color: var(--text-secondary);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 0.5rem;
}

.stat-card .value { font-size: 1.4rem; font-weight: 600; }
.stat-card .value.positive { color: var(--accent-green); }
.stat-card .value.negative { color: var(--accent-red); }
.stat-card .value.neutral { color: var(--text-primary); }

.chart-section {
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 1.5rem;
    margin-bottom: 2rem;
}

.chart-section h2 {
    font-size: 1.25rem;
    font-weight: 600;
    margin-bottom: 1rem;
    color: var(--text-primary);
}

.chart-container { width: 100%; height: 400px; }

.trades-section {
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 1.5rem;
    margin-bottom: 2rem;
    overflow-x: auto;
}

.trades-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
}

.trades-table th {
    background: var(--bg-tertiary);
    color: var(--text-secondary);
    font-weight: 600;
    text-align: left;
    padding: 0.75rem 1rem;
    border-bottom: 2px solid var(--border-color);
    text-transform: uppercase;
    font-size: 0.7rem;
    letter-spacing: 0.5px;
}

.trades-table td {
    padding: 0.6rem 1rem;
    border-bottom: 1px solid var(--border-color);
}

.trades-table tr:hover { background: var(--bg-tertiary); }
.trades-table .pnl-positive { color: var(--accent-green); font-weight: 600; }
.trades-table .pnl-negative { color: var(--accent-red); font-weight: 600; }

.regime-badge {
    display: inline-block;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
}

.regime-bull { background: rgba(63, 185, 80, 0.2); color: var(--accent-green); }
.regime-bear { background: rgba(248, 81, 73, 0.2); color: var(--accent-red); }
.regime-range { background: rgba(210, 153, 34, 0.2); color: var(--accent-yellow); }
.regime-crash { background: rgba(163, 113, 247, 0.2); color: var(--accent-purple); }
.regime-recovery { background: rgba(88, 166, 255, 0.2); color: var(--accent-blue); }

/* ============================================ */
/* PAGINATION STYLES                            */
/* ============================================ */

.pagination-container {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 1.5rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border-color);
    flex-wrap: wrap;
    gap: 1rem;
}

.pagination-info {
    color: var(--text-secondary);
    font-size: 0.85rem;
}

.pagination-controls {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
}

.pagination-btn {
    padding: 0.5rem 0.75rem;
    background: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    color: var(--text-primary);
    cursor: pointer;
    font-size: 0.85rem;
    transition: all 0.2s;
    min-width: 40px;
    text-align: center;
}

.pagination-btn:hover:not(:disabled) {
    background: var(--accent-blue);
    border-color: var(--accent-blue);
}

.pagination-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.pagination-btn.active {
    background: var(--accent-blue);
    border-color: var(--accent-blue);
}

.per-page-select {
    padding: 0.5rem;
    background: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    color: var(--text-primary);
    font-size: 0.85rem;
    cursor: pointer;
}

/* ============================================ */
/* ENHANCED MODAL STYLES                        */
/* ============================================ */

.modal-overlay {
    display: none;
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: rgba(0, 0, 0, 0.85);
    z-index: 1000;
    justify-content: center;
    align-items: flex-start;
    backdrop-filter: blur(4px);
    overflow-y: auto;
    padding: 2rem;
}

.modal-overlay.active {
    display: flex;
}

.modal {
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    max-width: 900px;
    width: 100%;
    position: relative;
    animation: modalSlideIn 0.3s ease;
    margin: auto;
}

@keyframes modalSlideIn {
    from {
        opacity: 0;
        transform: translateY(-20px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.modal-header {
    padding: 1.5rem;
    border-bottom: 1px solid var(--border-color);
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: var(--bg-secondary);
    border-radius: 12px 12px 0 0;
}

.modal-header.win {
    border-left: 4px solid var(--accent-green);
}

.modal-header.loss {
    border-left: 4px solid var(--accent-red);
}

.modal-header h3 {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 1.25rem;
}

.modal-close {
    background: none;
    border: none;
    color: var(--text-secondary);
    font-size: 1.5rem;
    cursor: pointer;
    padding: 0.5rem;
    transition: color 0.2s;
}

.modal-close:hover {
    color: var(--text-primary);
}

.modal-body {
    padding: 1.5rem;
}

.modal-section {
    margin-bottom: 1.5rem;
    padding-bottom: 1.5rem;
    border-bottom: 1px solid var(--border-color);
}

.modal-section:last-child {
    margin-bottom: 0;
    padding-bottom: 0;
    border-bottom: none;
}

.modal-section h4 {
    color: var(--text-secondary);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.modal-stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0.75rem;
}

.modal-stat {
    background: var(--bg-tertiary);
    padding: 0.75rem;
    border-radius: 6px;
    text-align: center;
}

.modal-stat .label {
    color: var(--text-muted);
    font-size: 0.7rem;
    text-transform: uppercase;
    margin-bottom: 0.25rem;
}

.modal-stat .value {
    font-size: 0.95rem;
    font-weight: 600;
}

/* Indicator Table in Modal */
.indicator-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
}

.indicator-table th {
    background: var(--bg-tertiary);
    color: var(--text-muted);
    font-size: 0.7rem;
    text-transform: uppercase;
    padding: 0.6rem;
    text-align: left;
}

.indicator-table td {
    padding: 0.6rem;
    border-bottom: 1px solid var(--bg-tertiary);
}

.indicator-table .delta-positive { color: var(--accent-green); }
.indicator-table .delta-negative { color: var(--accent-red); }
.indicator-table .delta-neutral { color: var(--text-muted); }

/* Trade Journey Chart */
.trade-journey-chart {
    height: 200px;
    background: var(--bg-tertiary);
    border-radius: 8px;
    margin-bottom: 1rem;
}

/* Analysis Cards */
.analysis-card {
    background: var(--bg-tertiary);
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 0.75rem;
}

.analysis-card.entry-analysis {
    border-left: 3px solid var(--accent-blue);
}

.analysis-card.exit-analysis {
    border-left: 3px solid var(--accent-purple);
}

.analysis-card.root-cause {
    border-left: 3px solid var(--accent-yellow);
}

.analysis-card.lessons {
    border-left: 3px solid var(--accent-cyan);
}

.analysis-card h5 {
    font-size: 0.85rem;
    font-weight: 600;
    margin-bottom: 0.75rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.analysis-card ul {
    list-style: none;
    margin: 0;
    padding: 0;
}

.analysis-card li {
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--bg-secondary);
    font-size: 0.85rem;
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
}

.analysis-card li:last-child {
    border-bottom: none;
}

.analysis-card .bullet {
    flex-shrink: 0;
    width: 20px;
    text-align: center;
}

/* Severity Badges */
.severity-badge {
    display: inline-block;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
}

.severity-minor { background: rgba(63, 185, 80, 0.2); color: var(--accent-green); }
.severity-moderate { background: rgba(210, 153, 34, 0.2); color: var(--accent-yellow); }
.severity-critical { background: rgba(248, 81, 73, 0.2); color: var(--accent-red); }

/* Progress Bars for Indicators */
.indicator-progress {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin: 0.5rem 0;
}

.indicator-progress .label {
    min-width: 80px;
    font-size: 0.8rem;
    color: var(--text-secondary);
}

.indicator-progress .bar-container {
    flex: 1;
    height: 8px;
    background: var(--bg-primary);
    border-radius: 4px;
    position: relative;
    overflow: hidden;
}

.indicator-progress .bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.5s ease;
}

.indicator-progress .bar-entry {
    position: absolute;
    height: 100%;
    background: var(--text-muted);
    opacity: 0.5;
}

.indicator-progress .bar-exit {
    position: absolute;
    height: 100%;
}

.indicator-progress .values {
    min-width: 100px;
    text-align: right;
    font-family: monospace;
    font-size: 0.8rem;
}

/* MTF Table */
.mtf-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
    margin-top: 0.5rem;
}

.mtf-table th {
    background: var(--bg-primary);
    color: var(--text-muted);
    padding: 0.5rem;
    text-align: center;
    font-size: 0.7rem;
}

.mtf-table td {
    padding: 0.5rem;
    text-align: center;
    border-bottom: 1px solid var(--bg-tertiary);
    font-family: monospace;
}

.mtf-table .tf-label {
    font-weight: 600;
    text-align: left;
}

/* Analyze button in table */
.analyze-btn {
    padding: 0.35rem 0.75rem;
    background: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    border-radius: 4px;
    color: var(--accent-blue);
    cursor: pointer;
    font-size: 0.75rem;
    transition: all 0.2s;
    display: flex;
    align-items: center;
    gap: 0.3rem;
}

.analyze-btn:hover {
    background: var(--accent-blue);
    border-color: var(--accent-blue);
    color: white;
}

/* Tabs for multi-config */
.tabs {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1.5rem;
    border-bottom: 1px solid var(--border-color);
    padding-bottom: 0.5rem;
    flex-wrap: wrap;
}

.tab {
    padding: 0.5rem 1rem;
    background: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    border-radius: 6px 6px 0 0;
    cursor: pointer;
    color: var(--text-secondary);
    font-size: 0.85rem;
    transition: all 0.2s;
}

.tab:hover { background: var(--bg-secondary); color: var(--text-primary); }
.tab.active { background: var(--accent-blue); color: white; border-color: var(--accent-blue); }

.tab-content { display: none; }
.tab-content.active { display: block; }

/* Ranking table */
.ranking-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 2rem;
}

.ranking-table th {
    background: var(--bg-tertiary);
    color: var(--text-secondary);
    padding: 0.75rem 1rem;
    text-align: left;
    font-size: 0.75rem;
    text-transform: uppercase;
}

.ranking-table td {
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border-color);
}

.ranking-table tr:first-child td { background: rgba(63, 185, 80, 0.1); }
.ranking-table tr:nth-child(2) td { background: rgba(88, 166, 255, 0.05); }

.medal { font-size: 1.2rem; }

.footer {
    text-align: center;
    padding-top: 2rem;
    margin-top: 2rem;
    border-top: 1px solid var(--border-color);
    color: var(--text-muted);
    font-size: 0.85rem;
}

.footer a { color: var(--accent-blue); text-decoration: none; }

/* Export buttons */
.export-buttons {
    display: flex;
    gap: 0.5rem;
    margin-top: 1rem;
}

.export-btn {
    padding: 0.5rem 1rem;
    background: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    color: var(--text-primary);
    cursor: pointer;
    font-size: 0.85rem;
    transition: all 0.2s;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.export-btn:hover {
    background: var(--accent-blue);
    border-color: var(--accent-blue);
}

@media (max-width: 768px) {
    body { padding: 1rem; }
    .stats-grid { grid-template-columns: repeat(2, 1fr); }
    .chart-container { height: 300px; }
    .modal-stats { grid-template-columns: repeat(2, 1fr); }
    .pagination-controls { justify-content: center; }
    .modal { max-width: 100%; }
}
"""


# =============================================================================
# JAVASCRIPT (ENHANCED WITH TRADE ANALYSIS)
# =============================================================================

CUSTOM_JS = """
// ===========================================
// GLOBAL STATE
// ===========================================

let currentPage = 1;
let perPage = 20;
let totalTrades = 0;
let allTrades = [];

// ===========================================
// INITIALIZATION
// ===========================================

document.addEventListener('DOMContentLoaded', function() {
    // Animate stats
    const stats = document.querySelectorAll('.stat-card .value');
    stats.forEach((stat, idx) => {
        stat.style.opacity = '0';
        stat.style.transform = 'translateY(10px)';
        setTimeout(() => {
            stat.style.transition = 'all 0.3s ease';
            stat.style.opacity = '1';
            stat.style.transform = 'translateY(0)';
        }, idx * 50);
    });
    
    // Tab switching
    const tabs = document.querySelectorAll('.tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', function() {
            const targetId = this.dataset.target;
            
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            
            this.classList.add('active');
            document.getElementById(targetId).classList.add('active');
        });
    });
    
    // Initialize trades data
    if (typeof tradesData !== 'undefined') {
        allTrades = tradesData;
        totalTrades = allTrades.length;
        renderTrades();
        renderPagination();
    }
    
    // Per page selector
    const perPageSelect = document.getElementById('per-page-select');
    if (perPageSelect) {
        perPageSelect.addEventListener('change', function() {
            perPage = parseInt(this.value);
            currentPage = 1;
            renderTrades();
            renderPagination();
        });
    }
    
    // Modal close handlers
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
        overlay.addEventListener('click', function(e) {
            if (e.target === this) {
                closeModal();
            }
        });
    });
    
    // ESC to close modal
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            closeModal();
        }
    });
});

// ===========================================
// PAGINATION
// ===========================================

function renderTrades() {
    const tbody = document.getElementById('trades-tbody');
    if (!tbody) return;
    
    const start = (currentPage - 1) * perPage;
    const end = Math.min(start + perPage, totalTrades);
    const pageTrades = allTrades.slice(start, end);
    
    let html = '';
    pageTrades.forEach((trade, idx) => {
        const globalIdx = start + idx;
        const pnlClass = trade.pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        const regimeClass = 'regime-' + trade.regime.toLowerCase();
        
        html += `
        <tr>
            <td>${trade.id}</td>
            <td><span class="regime-badge ${regimeClass}">${trade.regime}</span></td>
            <td>${trade.entryTime}</td>
            <td>$${trade.entryPrice}</td>
            <td>${trade.exitTime}</td>
            <td>$${trade.exitPrice}</td>
            <td class="${pnlClass}">${trade.pnl >= 0 ? '+' : ''}${trade.pnl.toFixed(2)}%</td>
            <td>${trade.exitReason}</td>
            <td>${trade.duration}m</td>
            <td>
                <button class="analyze-btn" onclick="openTradeModal(${globalIdx})">
                    <span>🔍</span> Analyze
                </button>
            </td>
        </tr>`;
    });
    
    tbody.innerHTML = html;
    
    // Update info text
    const infoEl = document.getElementById('pagination-info');
    if (infoEl) {
        infoEl.textContent = `Showing ${start + 1}-${end} of ${totalTrades} trades`;
    }
}

function renderPagination() {
    const container = document.getElementById('pagination-buttons');
    if (!container) return;
    
    const totalPages = Math.ceil(totalTrades / perPage);
    
    let html = '';
    
    // Previous button
    html += `<button class="pagination-btn" onclick="goToPage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>← Prev</button>`;
    
    // Page numbers
    const maxButtons = 7;
    let startPage = Math.max(1, currentPage - Math.floor(maxButtons / 2));
    let endPage = Math.min(totalPages, startPage + maxButtons - 1);
    
    if (endPage - startPage < maxButtons - 1) {
        startPage = Math.max(1, endPage - maxButtons + 1);
    }
    
    if (startPage > 1) {
        html += `<button class="pagination-btn" onclick="goToPage(1)">1</button>`;
        if (startPage > 2) {
            html += `<span style="color: var(--text-muted); padding: 0 0.5rem;">...</span>`;
        }
    }
    
    for (let i = startPage; i <= endPage; i++) {
        html += `<button class="pagination-btn ${i === currentPage ? 'active' : ''}" onclick="goToPage(${i})">${i}</button>`;
    }
    
    if (endPage < totalPages) {
        if (endPage < totalPages - 1) {
            html += `<span style="color: var(--text-muted); padding: 0 0.5rem;">...</span>`;
        }
        html += `<button class="pagination-btn" onclick="goToPage(${totalPages})">${totalPages}</button>`;
    }
    
    // Next button
    html += `<button class="pagination-btn" onclick="goToPage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>Next →</button>`;
    
    container.innerHTML = html;
}

function goToPage(page) {
    const totalPages = Math.ceil(totalTrades / perPage);
    if (page < 1 || page > totalPages) return;
    
    currentPage = page;
    renderTrades();
    renderPagination();
    
    // Scroll to table
    document.getElementById('trades-section').scrollIntoView({ behavior: 'smooth' });
}

// ===========================================
// ENHANCED MODAL
// ===========================================

function openTradeModal(tradeIndex) {
    const trade = allTrades[tradeIndex];
    if (!trade) return;
    
    const modal = document.getElementById('trade-modal');
    const overlay = document.getElementById('modal-overlay');
    
    if (!modal || !overlay) return;
    
    const isWin = trade.pnl >= 0;
    
    // Set header
    const header = modal.querySelector('.modal-header');
    header.className = 'modal-header ' + (isWin ? 'win' : 'loss');
    
    const title = modal.querySelector('.modal-header h3');
    title.innerHTML = isWin 
        ? `<span style="font-size: 1.5rem;">🟢</span> Trade #${trade.id} - WIN (+${trade.pnl.toFixed(2)}%)`
        : `<span style="font-size: 1.5rem;">🔴</span> Trade #${trade.id} - LOSS (${trade.pnl.toFixed(2)}%)`;
    
    // Set basic stats
    document.getElementById('modal-entry-time').textContent = trade.entryTime;
    document.getElementById('modal-exit-time').textContent = trade.exitTime;
    document.getElementById('modal-entry-price').textContent = '$' + trade.entryPrice;
    document.getElementById('modal-exit-price').textContent = '$' + trade.exitPrice;
    document.getElementById('modal-regime').innerHTML = `<span class="regime-badge regime-${trade.regime.toLowerCase()}">${trade.regime}</span>`;
    document.getElementById('modal-duration').textContent = trade.duration + ' min';
    document.getElementById('modal-exit-reason').textContent = formatExitReason(trade.exitReason);
    document.getElementById('modal-signal-score').textContent = trade.signalScore ? trade.signalScore.toFixed(1) + '/100' : 'N/A';
    document.getElementById('modal-peak-pnl').textContent = trade.peakPnl ? '+' + trade.peakPnl.toFixed(2) + '%' : 'N/A';
    
    // Set indicator snapshots
    const indicatorSection = document.getElementById('indicator-snapshots-section');
    if (trade.hasIndicators && trade.indicators) {
        indicatorSection.style.display = 'block';
        renderIndicatorTable(trade.indicators);
        renderIndicatorBars(trade.indicators);
    } else {
        indicatorSection.style.display = 'none';
    }
    
    // Set MTF section
    const mtfSection = document.getElementById('mtf-section');
    if (trade.hasMtf && trade.mtfData) {
        mtfSection.style.display = 'block';
        renderMtfTable(trade.mtfData);
    } else {
        mtfSection.style.display = 'none';
    }
    
    // Render trade journey chart
    renderTradeJourneyChart(trade);
    
    // Render entry analysis
    document.getElementById('entry-analysis-content').innerHTML = generateEntryAnalysis(trade);
    
    // Render exit analysis
    document.getElementById('exit-analysis-content').innerHTML = generateExitAnalysis(trade, isWin);
    
    // Render root cause
    document.getElementById('root-cause-content').innerHTML = generateRootCause(trade, isWin);
    
    // Render lessons
    document.getElementById('lessons-content').innerHTML = generateLessons(trade, isWin);
    
    // Show modal
    overlay.classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeModal() {
    const overlay = document.getElementById('modal-overlay');
    if (overlay) {
        overlay.classList.remove('active');
        document.body.style.overflow = '';
    }
}

function formatExitReason(reason) {
    const formatted = {
        'hard_sl': '🛑 Hard Stop Loss',
        'stop_loss': '🛑 Stop Loss',
        'trailing_sl': '📈 Trailing Stop',
        'trailing_stop': '📈 Trailing Stop',
        'take_profit': '🎯 Take Profit',
        'tp': '🎯 Take Profit',
        'timeout': '⏰ Timeout',
        'timeout_profit': '⏰ Timeout (Profit)',
        'timeout_missed': '⏰ Timeout (Missed)',
        'timeout_no_momentum': '⏰ Timeout (No Momentum)',
        'manual': '✋ Manual Close',
        'end_of_data': '📊 End of Data'
    };
    
    const lower = reason.toLowerCase();
    for (const [key, val] of Object.entries(formatted)) {
        if (lower.includes(key)) return val;
    }
    return reason;
}

// ===========================================
// INDICATOR RENDERING
// ===========================================

function renderIndicatorTable(indicators) {
    const tbody = document.getElementById('indicator-table-body');
    if (!tbody) return;
    
    let html = '';
    
    const rows = [
        { name: 'RSI', entry: indicators.entryRsi, exit: indicators.exitRsi, unit: '', precision: 1 },
        { name: 'ADX', entry: indicators.entryAdx, exit: indicators.exitAdx, unit: '', precision: 1 },
        { name: 'EMA Gap', entry: indicators.entryEmaGap, exit: indicators.exitEmaGap, unit: '%', precision: 2 },
        { name: 'Volume Ratio', entry: indicators.entryVolume, exit: indicators.exitVolume, unit: 'x', precision: 2 },
        { name: 'ATR %', entry: indicators.entryAtr, exit: indicators.exitAtr, unit: '%', precision: 2 },
    ];
    
    rows.forEach(row => {
        if (row.entry == null || row.exit == null) return;
        
        const delta = row.exit - row.entry;
        let deltaClass = 'delta-neutral';
        let deltaSign = '';
        
        if (Math.abs(delta) > 0.01) {
            if (delta > 0) {
                deltaClass = 'delta-positive';
                deltaSign = '+';
            } else {
                deltaClass = 'delta-negative';
            }
        }
        
        // Determine status icon
        let status = '✅';
        if (row.name === 'RSI') {
            if (Math.abs(delta) > 15) status = '⚠️';
            else if (Math.abs(delta) > 10) status = '⚡';
        } else if (row.name === 'ADX') {
            if (delta < -8) status = '⚠️';
            else if (delta < -5) status = '⚡';
        } else if (row.name === 'Volume Ratio') {
            if (delta < -1.0) status = '⚠️';
            else if (delta < -0.5) status = '⚡';
        }
        
        html += `
        <tr>
            <td style="font-weight: 500;">${row.name}</td>
            <td style="text-align: right; font-family: monospace;">${row.entry.toFixed(row.precision)}${row.unit}</td>
            <td style="text-align: right; font-family: monospace;">${row.exit.toFixed(row.precision)}${row.unit}</td>
            <td style="text-align: right; font-family: monospace;" class="${deltaClass}">${deltaSign}${delta.toFixed(row.precision)}${row.unit}</td>
            <td style="text-align: center; font-size: 1.2rem;">${status}</td>
        </tr>`;
    });
    
    tbody.innerHTML = html;
}

function renderIndicatorBars(indicators) {
    const container = document.getElementById('indicator-bars');
    if (!container) return;
    
    let html = '';
    
    const bars = [
        { name: 'RSI', entry: indicators.entryRsi, exit: indicators.exitRsi, min: 0, max: 100 },
        { name: 'ADX', entry: indicators.entryAdx, exit: indicators.exitAdx, min: 0, max: 60 },
        { name: 'Volume', entry: indicators.entryVolume, exit: indicators.exitVolume, min: 0, max: 3 },
    ];
    
    bars.forEach(bar => {
        if (bar.entry == null || bar.exit == null) return;
        
        const entryPct = Math.min(100, Math.max(0, ((bar.entry - bar.min) / (bar.max - bar.min)) * 100));
        const exitPct = Math.min(100, Math.max(0, ((bar.exit - bar.min) / (bar.max - bar.min)) * 100));
        
        const delta = bar.exit - bar.entry;
        let color = 'var(--text-muted)';
        let arrow = '→';
        if (delta > 0.5) {
            color = 'var(--accent-green)';
            arrow = '↗';
        } else if (delta < -0.5) {
            color = 'var(--accent-red)';
            arrow = '↘';
        }
        
        html += `
        <div class="indicator-progress">
            <span class="label">${bar.name}</span>
            <div class="bar-container">
                <div class="bar-entry" style="width: ${entryPct}%;"></div>
                <div class="bar-exit" style="width: ${exitPct}%; background: ${color};"></div>
            </div>
            <span class="values" style="color: ${color};">${bar.entry.toFixed(1)} ${arrow} ${bar.exit.toFixed(1)}</span>
        </div>`;
    });
    
    container.innerHTML = html;
}

function renderMtfTable(mtfData) {
    const tbody = document.getElementById('mtf-table-body');
    if (!tbody) return;
    
    let html = '';
    
    const timeframes = Object.keys(mtfData).sort((a, b) => {
        const order = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440};
        return (order[a] || 999) - (order[b] || 999);
    });
    
    timeframes.forEach(tf => {
        const data = mtfData[tf];
        if (!data) return;
        
        let health = '✅';
        let healthScore = 0;
        if (data.exitAdx > 25) healthScore++;
        if (data.exitVolume > 1.0) healthScore++;
        if (data.exitRsi >= 40 && data.exitRsi <= 65) healthScore++;
        
        if (healthScore < 2) health = '⚠️';
        else if (healthScore < 3) health = '⚡';
        
        html += `
        <tr>
            <td class="tf-label">${tf}</td>
            <td>${data.entryRsi?.toFixed(0) || '-'}</td>
            <td>${data.exitRsi?.toFixed(0) || '-'}</td>
            <td>${data.entryAdx?.toFixed(0) || '-'}</td>
            <td>${data.exitAdx?.toFixed(0) || '-'}</td>
            <td>${data.exitVolume?.toFixed(1) || '-'}x</td>
            <td>${health}</td>
        </tr>`;
    });
    
    tbody.innerHTML = html;
}

// ===========================================
// TRADE JOURNEY CHART
// ===========================================

function renderTradeJourneyChart(trade) {
    const container = document.getElementById('trade-journey-chart');
    if (!container) return;
    
    // Create data points for the journey
    const entryPrice = parseFloat(trade.entryPrice.replace(',', ''));
    const exitPrice = parseFloat(trade.exitPrice.replace(',', ''));
    const highPrice = trade.highPrice || Math.max(entryPrice, exitPrice) * 1.01;
    const lowPrice = trade.lowPrice || Math.min(entryPrice, exitPrice) * 0.99;
    
    // Time points
    const times = ['Entry', 'Peak/Trough', 'Exit'];
    
    // Price journey
    let prices;
    if (trade.pnl >= 0) {
        // Win: went up to high, then exit
        prices = [entryPrice, highPrice, exitPrice];
    } else {
        // Loss: went down to low, then exit
        prices = [entryPrice, lowPrice, exitPrice];
    }
    
    // Colors
    const lineColor = trade.pnl >= 0 ? '#3fb950' : '#f85149';
    
    const tracePrice = {
        x: times,
        y: prices,
        type: 'scatter',
        mode: 'lines+markers',
        name: 'Price',
        line: { color: lineColor, width: 3 },
        marker: { size: 12, color: lineColor },
        fill: 'tozeroy',
        fillcolor: trade.pnl >= 0 ? 'rgba(63, 185, 80, 0.1)' : 'rgba(248, 81, 73, 0.1)'
    };
    
    // Entry/Exit markers
    const traceMarkers = {
        x: ['Entry', 'Exit'],
        y: [entryPrice, exitPrice],
        type: 'scatter',
        mode: 'markers+text',
        name: 'Key Points',
        marker: { size: 16, color: [lineColor, lineColor], symbol: ['circle', 'square'] },
        text: ['Entry', 'Exit'],
        textposition: 'top center',
        textfont: { color: '#e6edf3', size: 11 },
        showlegend: false
    };
    
    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        margin: { t: 20, r: 20, b: 40, l: 60 },
        xaxis: { 
            showgrid: false, 
            tickfont: { color: '#8b949e' },
            fixedrange: true
        },
        yaxis: { 
            showgrid: true, 
            gridcolor: '#30363d', 
            tickfont: { color: '#8b949e' },
            tickformat: '$,.2f',
            fixedrange: true
        },
        showlegend: false,
        hovermode: 'x unified'
    };
    
    Plotly.newPlot(container, [tracePrice, traceMarkers], layout, { 
        responsive: true, 
        displayModeBar: false 
    });
}

// ===========================================
// ANALYSIS GENERATORS
// ===========================================

function generateEntryAnalysis(trade) {
    let items = [];
    
    // Signal Score Analysis
    if (trade.signalScore) {
        if (trade.signalScore >= 80) {
            items.push({ icon: '⭐', text: `<strong>Strong signal</strong> - Score ${trade.signalScore.toFixed(1)}/100 indicated high-quality setup` });
        } else if (trade.signalScore >= 70) {
            items.push({ icon: '✅', text: `<strong>Good signal</strong> - Score ${trade.signalScore.toFixed(1)}/100 met entry criteria` });
        } else if (trade.signalScore >= 60) {
            items.push({ icon: '⚡', text: `<strong>Moderate signal</strong> - Score ${trade.signalScore.toFixed(1)}/100 was borderline` });
        } else {
            items.push({ icon: '⚠️', text: `<strong>Weak signal</strong> - Score ${trade.signalScore.toFixed(1)}/100 was below optimal threshold` });
        }
    }
    
    // Regime Analysis
    const regime = trade.regime.toLowerCase();
    if (regime === 'bull') {
        items.push({ icon: '📈', text: '<strong>Bull market entry</strong> - Favorable conditions for long positions' });
    } else if (regime === 'bear') {
        items.push({ icon: '📉', text: '<strong>Bear market entry</strong> - Higher risk environment for longs' });
    } else if (regime === 'range') {
        items.push({ icon: '↔️', text: '<strong>Range-bound market</strong> - Sideways conditions can cause false signals' });
    } else if (regime === 'crash') {
        items.push({ icon: '💥', text: '<strong>Crash conditions</strong> - High volatility entry, elevated risk' });
    }
    
    // Indicator-based entry reasons
    if (trade.hasIndicators && trade.indicators) {
        const ind = trade.indicators;
        
        // EMA alignment
        if (ind.entryEmaGap > 0.3) {
            items.push({ icon: '📊', text: `<strong>Strong EMA alignment</strong> - ${ind.entryEmaGap.toFixed(2)}% gap indicated clear trend` });
        }
        
        // ADX strength
        if (ind.entryAdx >= 30) {
            items.push({ icon: '💪', text: `<strong>Strong trend (ADX ${ind.entryAdx.toFixed(0)})</strong> - Favorable for momentum entries` });
        }
        
        // RSI position
        if (ind.entryRsi >= 45 && ind.entryRsi <= 55) {
            items.push({ icon: '⚖️', text: `<strong>Neutral RSI (${ind.entryRsi.toFixed(0)})</strong> - Room for movement in either direction` });
        } else if (ind.entryRsi < 40) {
            items.push({ icon: '🔻', text: `<strong>Low RSI (${ind.entryRsi.toFixed(0)})</strong> - Potential bounce setup` });
        }
        
        // Volume confirmation
        if (ind.entryVolume >= 1.5) {
            items.push({ icon: '📢', text: `<strong>High volume (${ind.entryVolume.toFixed(1)}x)</strong> - Strong interest confirming move` });
        }
    }
    
    // Default if no specific reasons
    if (items.length === 0) {
        items.push({ icon: 'ℹ️', text: 'Entry based on technical signal criteria' });
    }
    
    return items.map(i => `<li><span class="bullet">${i.icon}</span><span>${i.text}</span></li>`).join('');
}

function generateExitAnalysis(trade, isWin) {
    let items = [];
    const exitReason = trade.exitReason.toLowerCase();
    
    // Exit reason specific analysis
    if (exitReason.includes('hard_sl') || exitReason.includes('stop_loss')) {
        items.push({ icon: '🛑', text: '<strong>Hard stop loss triggered</strong> - Price moved against position beyond risk threshold' });
        
        if (trade.hasIndicators && trade.indicators) {
            const adxDelta = trade.indicators.exitAdx - trade.indicators.entryAdx;
            if (adxDelta < -5) {
                items.push({ icon: '📉', text: `<strong>Trend weakened</strong> - ADX dropped by ${Math.abs(adxDelta).toFixed(1)} points` });
            }
        }
    }
    
    else if (exitReason.includes('trailing')) {
        if (isWin) {
            items.push({ icon: '📈', text: '<strong>Trailing stop locked profits</strong> - Captured gains while allowing room for movement' });
        } else {
            items.push({ icon: '📉', text: '<strong>Trailing stop after reversal</strong> - Price initially moved favorably then reversed' });
        }
        
        if (trade.peakPnl && trade.peakPnl > trade.pnl + 0.5) {
            items.push({ icon: '💡', text: `<strong>Gave back ${(trade.peakPnl - trade.pnl).toFixed(2)}%</strong> from peak before trail triggered` });
        }
    }
    
    else if (exitReason.includes('take_profit') || exitReason.includes('tp')) {
        items.push({ icon: '🎯', text: '<strong>Take profit target hit</strong> - Price reached predetermined target' });
    }
    
    else if (exitReason.includes('timeout')) {
        if (isWin) {
            items.push({ icon: '⏰', text: '<strong>Profitable timeout</strong> - Position held until time limit while in profit' });
        } else {
            items.push({ icon: '⏰', text: '<strong>Position timed out</strong> - Price failed to reach targets within time limit' });
        }
        
        if (exitReason.includes('no_momentum')) {
            items.push({ icon: '😴', text: '<strong>Lack of momentum</strong> - Price stagnated without clear direction' });
        } else if (exitReason.includes('missed')) {
            items.push({ icon: '😤', text: '<strong>Missed opportunity</strong> - Was in profit but didn\\'t capture gains' });
        }
    }
    
    // Indicator deterioration
    if (trade.hasIndicators && trade.indicators) {
        const ind = trade.indicators;
        
        const rsiDelta = ind.exitRsi - ind.entryRsi;
        if (rsiDelta > 15) {
            items.push({ icon: '🔥', text: `<strong>RSI spiked (+${rsiDelta.toFixed(1)})</strong> - Overbought conditions developed` });
        } else if (rsiDelta < -15) {
            items.push({ icon: '❄️', text: `<strong>RSI crashed (${rsiDelta.toFixed(1)})</strong> - Oversold conditions developed` });
        }
        
        const volDelta = ind.exitVolume - ind.entryVolume;
        if (volDelta < -0.5) {
            items.push({ icon: '🔇', text: `<strong>Volume dried up</strong> - Ratio dropped by ${Math.abs(volDelta).toFixed(2)}x` });
        }
    }
    
    if (items.length === 0) {
        items.push({ icon: 'ℹ️', text: 'Exit based on predefined exit criteria' });
    }
    
    return items.map(i => `<li><span class="bullet">${i.icon}</span><span>${i.text}</span></li>`).join('');
}

function generateRootCause(trade, isWin) {
    let severity = 'minor';
    let rootCause = '';
    let factors = [];
    
    const exitReason = trade.exitReason.toLowerCase();
    
    if (isWin) {
        // Win analysis
        if (exitReason.includes('take_profit') || exitReason.includes('tp')) {
            rootCause = '🎯 Target Achievement';
            factors.push('Signal correctly identified profitable opportunity');
            factors.push('Risk/reward setup was favorable');
        } else if (exitReason.includes('trailing')) {
            rootCause = '📈 Trend Capture';
            factors.push('Trailing stop successfully protected gains');
            factors.push('Position rode favorable momentum');
            
            if (trade.peakPnl && trade.peakPnl > trade.pnl + 1) {
                severity = 'moderate';
                factors.push(`Could have captured ${(trade.peakPnl - trade.pnl).toFixed(1)}% more with tighter trail`);
            }
        } else if (exitReason.includes('timeout')) {
            rootCause = '⏰ Time-Based Profit';
            factors.push('Position accumulated profit within time window');
            severity = 'moderate';
            factors.push('Consider: Could extended timeout have yielded more?');
        } else {
            rootCause = '✅ Successful Execution';
            factors.push('Trade executed according to plan');
        }
    } else {
        // Loss analysis
        if (exitReason.includes('hard_sl') || exitReason.includes('stop_loss')) {
            rootCause = '🛑 Stop Loss Defense';
            severity = 'critical';
            factors.push('Price moved against position thesis');
            factors.push('Stop loss prevented larger potential loss');
            
            if (trade.hasIndicators && trade.indicators) {
                const adxDelta = trade.indicators.exitAdx - trade.indicators.entryAdx;
                if (adxDelta < -5) {
                    factors.push('Trend strength deteriorated during trade');
                }
            }
            
            // Regime factor
            const regime = trade.regime.toLowerCase();
            if (regime === 'bear' || regime === 'crash') {
                factors.push('Entered during unfavorable market conditions');
            }
        } else if (exitReason.includes('trailing')) {
            rootCause = '📉 Reversal After Profit';
            severity = 'moderate';
            factors.push('Price reversed after initial favorable move');
            factors.push('Trailing stop limited losses but position turned negative');
        } else if (exitReason.includes('timeout')) {
            rootCause = '😴 No Momentum';
            severity = 'moderate';
            factors.push('Market failed to follow through on signal');
            factors.push('Position stagnated without reaching targets');
            
            if (trade.signalScore && trade.signalScore < 70) {
                factors.push('Signal score was below optimal - weaker setup');
            }
        } else {
            rootCause = '❌ Trade Did Not Work Out';
            factors.push('Market conditions did not align with expectations');
        }
    }
    
    const severityBadge = `<span class="severity-badge severity-${severity}">${severity.toUpperCase()}</span>`;
    
    let html = `
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem;">
            <span style="font-size: 1.1rem; font-weight: 600;">${rootCause}</span>
            ${severityBadge}
        </div>
        <ul style="list-style: none; padding: 0; margin: 0;">
    `;
    
    factors.forEach(f => {
        html += `<li style="padding: 0.4rem 0; border-bottom: 1px solid var(--bg-secondary); font-size: 0.85rem;">• ${f}</li>`;
    });
    
    html += '</ul>';
    
    return html;
}

function generateLessons(trade, isWin) {
    let lessons = [];
    
    const exitReason = trade.exitReason.toLowerCase();
    const regime = trade.regime.toLowerCase();
    
    if (isWin) {
        // Winning lessons
        lessons.push({
            icon: '✅',
            title: 'Replicate This Setup',
            text: `Look for similar conditions: ${trade.regime} regime, ${formatExitReason(trade.exitReason).replace(/[🛑📈🎯⏰✋📊]/g, '').trim()} exits`
        });
        
        if (trade.signalScore && trade.signalScore >= 75) {
            lessons.push({
                icon: '⭐',
                title: 'High-Quality Signals Pay Off',
                text: `Score ${trade.signalScore.toFixed(0)}+ signals show strong performance in ${trade.regime} conditions`
            });
        }
        
        if (exitReason.includes('trailing') && trade.peakPnl && trade.peakPnl > trade.pnl + 0.5) {
            lessons.push({
                icon: '💡',
                title: 'Consider Tighter Trail',
                text: `Lost ${(trade.peakPnl - trade.pnl).toFixed(2)}% from peak - a tighter trail might capture more`
            });
        }
    } else {
        // Losing lessons
        if (exitReason.includes('hard_sl') || exitReason.includes('stop_loss')) {
            lessons.push({
                icon: '🛡️',
                title: 'Stop Loss Protected Capital',
                text: 'Risk management worked - loss was contained to predefined level'
            });
            
            if (regime === 'bear' || regime === 'crash') {
                lessons.push({
                    icon: '⚠️',
                    title: 'Review Regime Filters',
                    text: `Consider stricter entry criteria or reduced size in ${regime} conditions`
                });
            }
        }
        
        if (exitReason.includes('timeout')) {
            lessons.push({
                icon: '🎯',
                title: 'Review Signal Quality',
                text: 'Low-momentum trades may need higher score threshold to filter'
            });
        }
        
        if (trade.signalScore && trade.signalScore < 70) {
            lessons.push({
                icon: '📊',
                title: 'Raise Minimum Score',
                text: `Score ${trade.signalScore.toFixed(0)} was borderline - consider 70+ minimum`
            });
        }
        
        if (trade.hasIndicators && trade.indicators) {
            const adxEntry = trade.indicators.entryAdx;
            if (adxEntry < 25) {
                lessons.push({
                    icon: '💪',
                    title: 'Wait for Stronger Trends',
                    text: `Entry ADX was ${adxEntry.toFixed(0)} - consider waiting for 25+ confirmation`
                });
            }
        }
    }
    
    // Always add regime-specific lesson
    lessons.push({
        icon: '🌍',
        title: 'Regime Awareness',
        text: `Document ${regime} market behavior for future ${isWin ? 'replication' : 'avoidance'}`
    });
    
    let html = '<ul style="list-style: none; padding: 0; margin: 0;">';
    
    lessons.forEach(l => {
        html += `
        <li style="padding: 0.75rem; background: var(--bg-secondary); border-radius: 6px; margin-bottom: 0.5rem;">
            <div style="display: flex; align-items: flex-start; gap: 0.75rem;">
                <span style="font-size: 1.2rem;">${l.icon}</span>
                <div>
                    <strong style="display: block; margin-bottom: 0.25rem;">${l.title}</strong>
                    <span style="color: var(--text-secondary); font-size: 0.85rem;">${l.text}</span>
                </div>
            </div>
        </li>`;
    });
    
    html += '</ul>';
    
    return html;
}

// ===========================================
// EXPORT
// ===========================================

function exportTableToCSV(tableId) {
    let csv = [];
    
    csv.push(['ID', 'Regime', 'Entry Time', 'Entry Price', 'Exit Time', 'Exit Price', 'P&L %', 'Exit Reason', 'Duration (min)', 'Signal Score'].join(','));
    
    allTrades.forEach(trade => {
        csv.push([
            trade.id,
            trade.regime,
            trade.entryTime,
            trade.entryPrice,
            trade.exitTime,
            trade.exitPrice,
            trade.pnl.toFixed(2),
            trade.exitReason,
            trade.duration,
            trade.signalScore || ''
        ].join(','));
    });
    
    const blob = new Blob([csv.join('\\n')], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'trades_export.csv';
    a.click();
}

function exportToJSON() {
    const blob = new Blob([JSON.stringify(allTrades, null, 2)], { type: 'application/json' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'trades_export.json';
    a.click();
}
"""


# =============================================================================
# TRADE ANALYZER (ENHANCED)
# =============================================================================


class TradeAnalyzer:
    """Analyze individual trades for comprehensive modal data."""

    @staticmethod
    def analyze_trade(trade: BacktestTrade, fee_pct: float) -> Dict:
        """
        Analyze a trade and return comprehensive data for modal.

        Returns dict with all data needed for enhanced modal:
        - Basic info (id, prices, times, pnl)
        - Indicator snapshots if available
        - MTF data if available
        - High/low prices for chart
        """
        pnl = trade.net_pnl_pct(fee_pct)

        # Basic data
        data = {
            "id": trade.trade_id,
            "regime": trade.entry_regime.upper() if trade.entry_regime else "UNKNOWN",
            "entryTime": trade.entry_time.strftime("%Y-%m-%d %H:%M")
            if trade.entry_time
            else "",
            "exitTime": trade.exit_time.strftime("%Y-%m-%d %H:%M")
            if trade.exit_time
            else "",
            "entryPrice": f"{trade.entry_price:,.2f}",
            "exitPrice": f"{trade.exit_price:,.2f}",
            "pnl": round(pnl, 2),
            "exitReason": trade.exit_reason or "unknown",
            "duration": int(trade.hold_time_minutes()),
            "signalScore": trade.entry_score,
            "peakPnl": round(trade.highest_pnl_pct, 2),
            "highPrice": trade.highest_price,
            "lowPrice": None
            if trade.lowest_price == float("inf")
            else trade.lowest_price,
        }

        # Indicator snapshots
        data["hasIndicators"] = False
        if trade.entry_indicators and trade.exit_indicators:
            data["hasIndicators"] = True

            entry_ind = trade.entry_indicators
            exit_ind = trade.exit_indicators

            # Calculate EMA gaps
            try:
                entry_ema_gap = (
                    (entry_ind.ema9 - entry_ind.ema21) / entry_ind.ema21 * 100
                )
                exit_ema_gap = (exit_ind.ema9 - exit_ind.ema21) / exit_ind.ema21 * 100
            except (ZeroDivisionError, AttributeError):
                entry_ema_gap = 0
                exit_ema_gap = 0

            data["indicators"] = {
                "entryRsi": entry_ind.rsi,
                "exitRsi": exit_ind.rsi,
                "entryAdx": entry_ind.adx,
                "exitAdx": exit_ind.adx,
                "entryEmaGap": entry_ema_gap,
                "exitEmaGap": exit_ema_gap,
                "entryVolume": entry_ind.volume_ratio,
                "exitVolume": exit_ind.volume_ratio,
                "entryAtr": entry_ind.atr_pct,
                "exitAtr": exit_ind.atr_pct,
            }

        # MTF data
        data["hasMtf"] = False
        if trade.entry_mtf_indicators and trade.exit_mtf_indicators:
            data["hasMtf"] = True

            mtf_data = {}
            for tf in trade.entry_mtf_indicators.keys():
                entry_mtf = trade.entry_mtf_indicators.get(tf)
                exit_mtf = trade.exit_mtf_indicators.get(tf)

                if entry_mtf and exit_mtf:
                    mtf_data[tf] = {
                        "entryRsi": entry_mtf.rsi,
                        "exitRsi": exit_mtf.rsi,
                        "entryAdx": entry_mtf.adx,
                        "exitAdx": exit_mtf.adx,
                        "entryVolume": entry_mtf.volume_ratio,
                        "exitVolume": exit_mtf.volume_ratio,
                    }

            data["mtfData"] = mtf_data

        return data


# =============================================================================
# HTML REPORTER - SINGLE RESULT
# =============================================================================


class HTMLReporter:
    """
    Generate interactive HTML reports with Plotly charts.

    Features:
    - Paginated trade history
    - Enhanced "Why I Won/Lost" modal with:
      - Indicator snapshots
      - Trade journey chart
      - Detailed entry analysis
      - Detailed exit analysis
      - Root cause breakdown
      - Actionable lessons
    - Auto-open option
    """

    def __init__(self, result: BacktestResult):
        if not PLOTLY_AVAILABLE:
            raise ImportError("Plotly required. Install: pip install plotly")

        self.result = result
        self.fee_pct = result.config.total_fee_pct

    def generate(
        self,
        output_path: Optional[str] = None,
        include_trades: bool = True,
        auto_open: bool = False,
    ) -> str:
        """Generate HTML report."""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"backtest_report_{self.result.symbol}_{timestamp}.html"

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        html = self._build_html(include_trades)
        output_path.write_text(html, encoding="utf-8")

        log.info(f"Report generated: {output_path}")

        if auto_open:
            try:
                webbrowser.open(f"file://{output_path.absolute()}")
                log.info("Report opened in browser")
            except Exception as e:
                log.warning(f"Could not auto-open report: {e}")

        return str(output_path)

    def _build_html(self, include_trades: bool) -> str:
        """Build complete HTML document."""
        equity_chart = self._create_equity_chart()
        pnl_chart = self._create_pnl_distribution()
        regime_chart = self._create_regime_pie()
        monthly_chart = self._create_monthly_returns()

        # Prepare trades data for JavaScript (with full analysis)
        trades_data = []
        if include_trades and self.result.trades:
            for trade in self.result.trades:
                trades_data.append(TradeAnalyzer.analyze_trade(trade, self.fee_pct))

        trades_json = json.dumps(trades_data)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Backtest Report - {self.result.symbol}</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>{CUSTOM_CSS}</style>
</head>
<body>
    <div class="container">
        {self._build_header()}
        {self._build_stats_grid()}
        
        <div class="chart-section">
            <h2>📈 Equity Curve</h2>
            <div id="equity-chart" class="chart-container"></div>
        </div>
        
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-bottom: 2rem;">
            <div class="chart-section" style="margin-bottom: 0;">
                <h2>📊 P&L Distribution</h2>
                <div id="pnl-chart" class="chart-container" style="height: 280px;"></div>
            </div>
            <div class="chart-section" style="margin-bottom: 0;">
                <h2>🌍 Regime Breakdown</h2>
                <div id="regime-chart" class="chart-container" style="height: 280px;"></div>
            </div>
            <div class="chart-section" style="margin-bottom: 0;">
                <h2>📅 Monthly Returns</h2>
                <div id="monthly-chart" class="chart-container" style="height: 280px;"></div>
            </div>
        </div>
        
        {self._build_trades_section() if include_trades else ""}
        
        {self._build_footer()}
    </div>
    
    {self._build_enhanced_modal()}
    
    <script>
const tradesData = {trades_json};

{CUSTOM_JS}
{equity_chart}
{pnl_chart}
{regime_chart}
{monthly_chart}
    </script>
</body>
</html>"""

    def _build_header(self) -> str:
        return f"""
        <div class="header">
            <h1>📈 Backtest Report</h1>
            <p class="subtitle">{self.result.symbol} • {self.result.interval} • {self.result.period_name}</p>
            <p class="period">{self.result.start_date} → {self.result.end_date}</p>
        </div>"""

    def _build_stats_grid(self) -> str:
        pnl = self.result.total_pnl_pct()
        pnl_class = "positive" if pnl > 0 else "negative"
        wr_class = "positive" if self.result.winrate() > 50 else "negative"
        pf_class = "positive" if self.result.profit_factor() > 1 else "negative"
        sh_class = "positive" if self.result.sharpe_ratio() > 0 else "negative"

        return f"""
        <div class="stats-grid">
            <div class="stat-card">
                <div class="label">Total P&L</div>
                <div class="value {pnl_class}">{pnl:+.2f}%</div>
            </div>
            <div class="stat-card">
                <div class="label">Final Balance</div>
                <div class="value neutral">${self.result.final_balance:,.2f}</div>
            </div>
            <div class="stat-card">
                <div class="label">Total Trades</div>
                <div class="value neutral">{self.result.total_trades()}</div>
            </div>
            <div class="stat-card">
                <div class="label">Win Rate</div>
                <div class="value {wr_class}">{self.result.winrate():.1f}%</div>
            </div>
            <div class="stat-card">
                <div class="label">Profit Factor</div>
                <div class="value {pf_class}">{self.result.profit_factor():.2f}</div>
            </div>
            <div class="stat-card">
                <div class="label">Max Drawdown</div>
                <div class="value negative">{self.result.max_drawdown_pct():.2f}%</div>
            </div>
            <div class="stat-card">
                <div class="label">Sharpe Ratio</div>
                <div class="value {sh_class}">{self.result.sharpe_ratio():.2f}</div>
            </div>
            <div class="stat-card">
                <div class="label">Expectancy</div>
                <div class="value {"positive" if self.result.expectancy() > 0 else "negative"}">{self.result.expectancy():.2f}%</div>
            </div>
        </div>"""

    def _build_trades_section(self) -> str:
        """Build trades section with pagination."""
        total_trades = self.result.total_trades()

        return f"""
        <div class="trades-section" id="trades-section">
            <h2>📋 Trade History ({total_trades} trades)</h2>
            
            <table class="trades-table" id="trades-table">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Regime</th>
                        <th>Entry</th>
                        <th>Entry $</th>
                        <th>Exit</th>
                        <th>Exit $</th>
                        <th>P&L</th>
                        <th>Reason</th>
                        <th>Duration</th>
                        <th>Analysis</th>
                    </tr>
                </thead>
                <tbody id="trades-tbody">
                </tbody>
            </table>
            
            <div class="pagination-container">
                <div class="pagination-info" id="pagination-info">
                    Showing 1-20 of {total_trades} trades
                </div>
                
                <div style="display: flex; align-items: center; gap: 1rem;">
                    <div>
                        <label for="per-page-select" style="color: var(--text-secondary); font-size: 0.85rem; margin-right: 0.5rem;">
                            Per page:
                        </label>
                        <select id="per-page-select" class="per-page-select">
                            <option value="10">10</option>
                            <option value="20" selected>20</option>
                            <option value="50">50</option>
                            <option value="100">100</option>
                        </select>
                    </div>
                    
                    <div class="pagination-controls" id="pagination-buttons">
                    </div>
                </div>
            </div>
            
            <div class="export-buttons">
                <button class="export-btn" onclick="exportTableToCSV('trades-table')">
                    📥 Export CSV
                </button>
                <button class="export-btn" onclick="exportToJSON()">
                    📄 Export JSON
                </button>
            </div>
        </div>"""

    def _build_enhanced_modal(self) -> str:
        """Build enhanced trade analysis modal."""
        return """
        <div id="modal-overlay" class="modal-overlay">
            <div id="trade-modal" class="modal">
                <div class="modal-header">
                    <h3>Trade Analysis</h3>
                    <button class="modal-close" onclick="closeModal()">×</button>
                </div>
                
                <div class="modal-body">
                    <!-- Basic Stats -->
                    <div class="modal-section">
                        <h4>📊 Trade Overview</h4>
                        <div class="modal-stats">
                            <div class="modal-stat">
                                <div class="label">Entry</div>
                                <div class="value" id="modal-entry-time">-</div>
                            </div>
                            <div class="modal-stat">
                                <div class="label">Exit</div>
                                <div class="value" id="modal-exit-time">-</div>
                            </div>
                            <div class="modal-stat">
                                <div class="label">Entry Price</div>
                                <div class="value" id="modal-entry-price">-</div>
                            </div>
                            <div class="modal-stat">
                                <div class="label">Exit Price</div>
                                <div class="value" id="modal-exit-price">-</div>
                            </div>
                            <div class="modal-stat">
                                <div class="label">Regime</div>
                                <div class="value" id="modal-regime">-</div>
                            </div>
                            <div class="modal-stat">
                                <div class="label">Duration</div>
                                <div class="value" id="modal-duration">-</div>
                            </div>
                            <div class="modal-stat">
                                <div class="label">Exit Reason</div>
                                <div class="value" id="modal-exit-reason">-</div>
                            </div>
                            <div class="modal-stat">
                                <div class="label">Signal Score</div>
                                <div class="value" id="modal-signal-score">-</div>
                            </div>
                        </div>
                        
                        <!-- Peak P&L -->
                        <div style="margin-top: 1rem; padding: 0.75rem; background: var(--bg-tertiary); border-radius: 6px; display: flex; justify-content: space-between; align-items: center;">
                            <span style="color: var(--text-secondary);">Peak P&L Reached:</span>
                            <span style="font-weight: 600; color: var(--accent-green);" id="modal-peak-pnl">-</span>
                        </div>
                    </div>
                    
                    <!-- Trade Journey Chart -->
                    <div class="modal-section">
                        <h4>📈 Trade Journey</h4>
                        <div id="trade-journey-chart" class="trade-journey-chart"></div>
                    </div>
                    
                    <!-- Indicator Snapshots -->
                    <div class="modal-section" id="indicator-snapshots-section" style="display: none;">
                        <h4>🔬 Indicator Snapshots (Entry → Exit)</h4>
                        
                        <table class="indicator-table">
                            <thead>
                                <tr>
                                    <th>Indicator</th>
                                    <th style="text-align: right;">Entry</th>
                                    <th style="text-align: right;">Exit</th>
                                    <th style="text-align: right;">Delta</th>
                                    <th style="text-align: center;">Status</th>
                                </tr>
                            </thead>
                            <tbody id="indicator-table-body">
                            </tbody>
                        </table>
                        
                        <div id="indicator-bars" style="margin-top: 1rem;"></div>
                    </div>
                    
                    <!-- MTF Section -->
                    <div class="modal-section" id="mtf-section" style="display: none;">
                        <h4>🌍 Multi-Timeframe Analysis</h4>
                        <table class="mtf-table">
                            <thead>
                                <tr>
                                    <th>TF</th>
                                    <th>Entry RSI</th>
                                    <th>Exit RSI</th>
                                    <th>Entry ADX</th>
                                    <th>Exit ADX</th>
                                    <th>Exit Vol</th>
                                    <th>Health</th>
                                </tr>
                            </thead>
                            <tbody id="mtf-table-body">
                            </tbody>
                        </table>
                    </div>
                    
                    <!-- Entry Analysis -->
                    <div class="modal-section">
                        <div class="analysis-card entry-analysis">
                            <h5>📥 Why We Entered</h5>
                            <ul id="entry-analysis-content">
                            </ul>
                        </div>
                    </div>
                    
                    <!-- Exit Analysis -->
                    <div class="modal-section">
                        <div class="analysis-card exit-analysis">
                            <h5>📤 Why We Exited</h5>
                            <ul id="exit-analysis-content">
                            </ul>
                        </div>
                    </div>
                    
                    <!-- Root Cause -->
                    <div class="modal-section">
                        <div class="analysis-card root-cause">
                            <h5>🎯 Root Cause Analysis</h5>
                            <div id="root-cause-content">
                            </div>
                        </div>
                    </div>
                    
                    <!-- Lessons -->
                    <div class="modal-section">
                        <div class="analysis-card lessons">
                            <h5>💡 Key Lessons</h5>
                            <div id="lessons-content">
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>"""

    def _build_footer(self) -> str:
        return f"""
        <div class="footer">
            <p>Generated by <strong>SignalBolt</strong> • {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
            <p><a href="https://github.com/yourusername/signalbolt">github.com/signalbolt</a></p>
        </div>"""

    def _create_equity_chart(self) -> str:
        if not self.result.equity_curve:
            return ""

        times = [t.isoformat() for t, _ in self.result.equity_curve]
        values = [v for _, v in self.result.equity_curve]

        return f"""
Plotly.newPlot('equity-chart', [{{
    x: {json.dumps(times)},
    y: {json.dumps(values)},
    type: 'scatter',
    mode: 'lines',
    fill: 'tozeroy',
    fillcolor: 'rgba(88, 166, 255, 0.1)',
    line: {{ color: '#58a6ff', width: 2 }},
    name: 'Equity'
}}], {{
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    margin: {{ t: 20, r: 20, b: 50, l: 60 }},
    xaxis: {{ showgrid: true, gridcolor: '#30363d', tickfont: {{ color: '#8b949e' }} }},
    yaxis: {{ showgrid: true, gridcolor: '#30363d', tickfont: {{ color: '#8b949e' }}, tickformat: '$,.0f' }},
    hovermode: 'x unified'
}}, {{responsive: true}});"""

    def _create_pnl_distribution(self) -> str:
        if not self.result.trades:
            return ""

        pnls = [t.net_pnl_pct(self.fee_pct) for t in self.result.trades]

        return f"""
Plotly.newPlot('pnl-chart', [{{
    x: {json.dumps(pnls)},
    type: 'histogram',
    marker: {{ color: '#58a6ff' }},
    nbinsx: 25
}}], {{
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    margin: {{ t: 10, r: 10, b: 40, l: 40 }},
    xaxis: {{ title: 'P&L %', showgrid: true, gridcolor: '#30363d', tickfont: {{ color: '#8b949e' }} }},
    yaxis: {{ title: 'Count', showgrid: true, gridcolor: '#30363d', tickfont: {{ color: '#8b949e' }} }}
}}, {{responsive: true}});"""

    def _create_regime_pie(self) -> str:
        if not self.result.regime_distribution:
            return ""

        labels = [l.upper() for l in self.result.regime_distribution.keys()]
        values = list(self.result.regime_distribution.values())
        colors = {
            "BULL": "#3fb950",
            "BEAR": "#f85149",
            "RANGE": "#d29922",
            "CRASH": "#a371f7",
            "RECOVERY": "#58a6ff",
        }
        marker_colors = [colors.get(l, "#8b949e") for l in labels]

        return f"""
Plotly.newPlot('regime-chart', [{{
    labels: {json.dumps(labels)},
    values: {json.dumps(values)},
    type: 'pie',
    hole: 0.4,
    marker: {{ colors: {json.dumps(marker_colors)}, line: {{ color: '#0d1117', width: 2 }} }},
    textinfo: 'percent',
    textfont: {{ color: '#e6edf3' }}
}}], {{
    paper_bgcolor: 'transparent',
    margin: {{ t: 10, r: 10, b: 30, l: 10 }},
    showlegend: true,
    legend: {{ font: {{ color: '#8b949e' }}, orientation: 'h', y: -0.1 }}
}}, {{responsive: true}});"""

    def _create_monthly_returns(self) -> str:
        if not self.result.trades:
            return ""

        monthly = {}
        for t in self.result.trades:
            if t.exit_time:
                key = t.exit_time.strftime("%Y-%m")
                monthly[key] = monthly.get(key, 0) + t.net_pnl_pct(self.fee_pct)

        months = sorted(monthly.keys())
        values = [monthly[m] for m in months]
        colors = ["#3fb950" if v > 0 else "#f85149" for v in values]

        return f"""
Plotly.newPlot('monthly-chart', [{{
    x: {json.dumps(months)},
    y: {json.dumps(values)},
    type: 'bar',
    marker: {{ color: {json.dumps(colors)} }}
}}], {{
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    margin: {{ t: 10, r: 10, b: 60, l: 40 }},
    xaxis: {{ showgrid: false, tickfont: {{ color: '#8b949e' }}, tickangle: -45 }},
    yaxis: {{ title: '%', showgrid: true, gridcolor: '#30363d', tickfont: {{ color: '#8b949e' }}, zeroline: true, zerolinecolor: '#30363d' }}
}}, {{responsive: true}});"""


# =============================================================================
# MULTI-CONFIG HTML REPORTER
# =============================================================================


class MultiConfigHTMLReporter:
    """Generate comparison HTML report for multiple configs."""

    def __init__(self, mc_result):
        if not PLOTLY_AVAILABLE:
            raise ImportError("Plotly required. Install: pip install plotly")

        self.mc_result = mc_result

    def generate(
        self, output_path: Optional[str] = None, auto_open: bool = False
    ) -> str:
        """Generate comparison HTML report."""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"config_comparison_{self.mc_result.symbol}_{timestamp}.html"

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        html = self._build_html()
        output_path.write_text(html, encoding="utf-8")

        log.info(f"Comparison report generated: {output_path}")

        if auto_open:
            try:
                webbrowser.open(f"file://{output_path.absolute()}")
            except Exception as e:
                log.warning(f"Could not auto-open report: {e}")

        return str(output_path)

    def _build_html(self) -> str:
        """Build comparison HTML."""
        valid_configs = [c for c in self.mc_result.configs if c.result is not None]
        valid_configs.sort(key=lambda x: x.rank)

        tabs_html = '<div class="tabs">'
        tabs_html += '<div class="tab active" data-target="overview">🏆 Overview</div>'
        for cfg in valid_configs:
            tabs_html += f'<div class="tab" data-target="config-{cfg.config_name}">{cfg.config_name}</div>'
        tabs_html += "</div>"

        overview_content = self._build_overview_tab(valid_configs)
        config_tabs = ""
        for cfg in valid_configs:
            config_tabs += self._build_config_tab(cfg)

        comparison_chart = self._create_comparison_chart(valid_configs)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Config Comparison - {self.mc_result.symbol}</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>{CUSTOM_CSS}</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>⚖️ Config Comparison Report</h1>
            <p class="subtitle">{self.mc_result.symbol} • {len(valid_configs)} Configurations</p>
            <p class="period">{self.mc_result.start_date} → {self.mc_result.end_date}</p>
        </div>
        
        {tabs_html}
        
        <div id="overview" class="tab-content active">
            {overview_content}
        </div>
        
        {config_tabs}
        
        <div class="footer">
            <p>Generated by <strong>SignalBolt</strong> • {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
    </div>
    
    <script>
{CUSTOM_JS}
{comparison_chart}
    </script>
</body>
</html>"""

    def _build_overview_tab(self, configs: List) -> str:
        """Build overview with ranking table."""
        rows = ""
        for cfg in configs:
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(cfg.rank, "")
            ret_class = "pnl-positive" if cfg.total_return > 0 else "pnl-negative"

            rows += f"""
            <tr>
                <td><span class="medal">{medal}</span> {cfg.rank}</td>
                <td><strong>{cfg.config_name}</strong></td>
                <td class="{ret_class}">{cfg.total_return:+.2f}%</td>
                <td>{cfg.total_trades}</td>
                <td>{cfg.winrate:.1f}%</td>
                <td>{cfg.profit_factor:.2f}</td>
                <td>{cfg.sharpe_ratio:.2f}</td>
                <td>{cfg.max_drawdown:.2f}%</td>
                <td><strong>{cfg.score:.1f}</strong></td>
            </tr>"""

        return f"""
        <div class="chart-section">
            <h2>🏆 Config Ranking</h2>
            <table class="ranking-table" id="ranking-table">
                <thead>
                    <tr>
                        <th>Rank</th>
                        <th>Config</th>
                        <th>Return</th>
                        <th>Trades</th>
                        <th>Win Rate</th>
                        <th>PF</th>
                        <th>Sharpe</th>
                        <th>Max DD</th>
                        <th>Score</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
        
        <div class="chart-section">
            <h2>📊 Returns Comparison</h2>
            <div id="comparison-chart" class="chart-container"></div>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="label">Best Return</div>
                <div class="value positive">{self.mc_result.best_return_config}</div>
            </div>
            <div class="stat-card">
                <div class="label">Best Sharpe</div>
                <div class="value neutral">{self.mc_result.best_sharpe_config}</div>
            </div>
            <div class="stat-card">
                <div class="label">Lowest DD</div>
                <div class="value neutral">{self.mc_result.best_drawdown_config}</div>
            </div>
            <div class="stat-card">
                <div class="label">Most Trades</div>
                <div class="value neutral">{self.mc_result.most_trades_config}</div>
            </div>
        </div>"""

    def _build_config_tab(self, cfg) -> str:
        """Build individual config tab."""
        if cfg.result is None:
            return f"""
            <div id="config-{cfg.config_name}" class="tab-content">
                <p>Error: {cfg.error}</p>
            </div>"""

        pnl_class = "positive" if cfg.total_return > 0 else "negative"

        return f"""
        <div id="config-{cfg.config_name}" class="tab-content">
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="label">Return</div>
                    <div class="value {pnl_class}">{cfg.total_return:+.2f}%</div>
                </div>
                <div class="stat-card">
                    <div class="label">Trades</div>
                    <div class="value neutral">{cfg.total_trades}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Win Rate</div>
                    <div class="value neutral">{cfg.winrate:.1f}%</div>
                </div>
                <div class="stat-card">
                    <div class="label">Profit Factor</div>
                    <div class="value neutral">{cfg.profit_factor:.2f}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Sharpe</div>
                    <div class="value neutral">{cfg.sharpe_ratio:.2f}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Max DD</div>
                    <div class="value negative">{cfg.max_drawdown:.2f}%</div>
                </div>
                <div class="stat-card">
                    <div class="label">Expectancy</div>
                    <div class="value neutral">{cfg.expectancy:.2f}%</div>
                </div>
                <div class="stat-card">
                    <div class="label">Score</div>
                    <div class="value neutral">{cfg.score:.1f}</div>
                </div>
            </div>
        </div>"""

    def _create_comparison_chart(self, configs: List) -> str:
        """Create returns comparison bar chart."""
        names = [c.config_name for c in configs]
        returns = [c.total_return for c in configs]
        colors = ["#3fb950" if r > 0 else "#f85149" for r in returns]

        return f"""
Plotly.newPlot('comparison-chart', [{{
    x: {json.dumps(names)},
    y: {json.dumps(returns)},
    type: 'bar',
    marker: {{ color: {json.dumps(colors)} }},
    text: {json.dumps([f"{r:+.2f}%" for r in returns])},
    textposition: 'outside'
}}], {{
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    margin: {{ t: 40, r: 20, b: 80, l: 60 }},
    xaxis: {{ showgrid: false, tickfont: {{ color: '#8b949e' }}, tickangle: -30 }},
    yaxis: {{ title: 'Return %', showgrid: true, gridcolor: '#30363d', tickfont: {{ color: '#8b949e' }}, zeroline: true, zerolinecolor: '#58a6ff' }}
}}, {{responsive: true}});"""


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def generate_html_report(
    result: BacktestResult, output_path: Optional[str] = None, auto_open: bool = False
) -> str:
    """Convenience function for single result."""
    reporter = HTMLReporter(result)
    return reporter.generate(output_path, auto_open=auto_open)


def generate_comparison_report(
    mc_result, output_path: Optional[str] = None, auto_open: bool = False
) -> str:
    """Convenience function for multi-config comparison."""
    reporter = MultiConfigHTMLReporter(mc_result)
    return reporter.generate(output_path, auto_open=auto_open)


def ask_and_open_report(report_path: str) -> bool:
    """Ask user if they want to open the report."""
    try:
        response = input("\n📊 Open report in browser? [Y/n]: ").strip().lower()

        if response in ("", "y", "yes", "tak", "t"):
            webbrowser.open(f"file://{Path(report_path).absolute()}")
            print("✅ Report opened in browser")
            return True
        else:
            print(f"📁 Report saved to: {report_path}")
            return False

    except Exception as e:
        log.warning(f"Could not open report: {e}")
        print(f"📁 Report saved to: {report_path}")
        return False
