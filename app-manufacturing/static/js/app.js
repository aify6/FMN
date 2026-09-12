(function () {
  "use strict";

  var MACHINES = JSON.parse(document.getElementById("machine-data").textContent);
  var machineById = {};
  for (var i = 0; i < MACHINES.length; i++) machineById[MACHINES[i].machine_id] = MACHINES[i];

  var activeFilter = "all";
  var selectedId = null;
  var charts = {};

  var tableBody = document.querySelector("#machine-table tbody");
  var emptyState = document.getElementById("empty-state");
  var detailPanel = document.getElementById("detail-panel");
  var detailBackdrop = document.getElementById("detail-backdrop");
  var tiles = document.querySelectorAll(".stat-tile");

  function applyFilter() {
    var rows = tableBody.querySelectorAll("tr.row");
    var visibleCount = 0;
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var show = activeFilter === "all" || row.getAttribute("data-flag") === activeFilter;
      row.style.display = show ? "" : "none";
      if (show) visibleCount++;
    }
    emptyState.style.display = visibleCount === 0 ? "" : "none";
  }

  for (var t = 0; t < tiles.length; t++) {
    tiles[t].addEventListener("click", function () {
      var f = this.getAttribute("data-filter");
      activeFilter = activeFilter === f ? "all" : f;
      for (var j = 0; j < tiles.length; j++) tiles[j].classList.remove("active");
      if (activeFilter !== "all") this.classList.add("active");
      applyFilter();
    });
  }

  function destroyChart(id) {
    if (charts[id]) {
      charts[id].destroy();
      delete charts[id];
    }
  }

  function lineChart(canvasId, values, color) {
    var ctx = document.getElementById(canvasId);
    if (!ctx) return;
    ctx.style.height = "120px";
    ctx.style.width = "100%";
    destroyChart(canvasId);
    charts[canvasId] = new Chart(ctx, {
      type: "line",
      data: {
        labels: values.map(function (_, i) { return i; }),
        datasets: [{
          data: values,
          borderColor: color,
          backgroundColor: color + "30",
          fill: true,
          cubicInterpolationMode: "monotone",
          tension: 0.35,
          pointRadius: 0,
          pointHoverRadius: 3,
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        resizeDelay: 0,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: { enabled: true } },
        layout: { padding: 0 },
        scales: {
          x: { display: false },
          y: { display: false },
        },
      },
    });
    charts[canvasId].resize();
  }

  function fmt(v) { return v === null || v === undefined ? "\u2014" : v; }

  function closeDetail() {
    detailPanel.classList.remove("open");
    detailBackdrop.classList.remove("open");
    selectedId = null;
    highlightSelectedRow();
  }

  function renderDetail(m) {
    var riskSeries = m.risk_trend_14d.map(function (p) { return p.risk_score; });
    var tempSeries = m.sensor_history_72h.map(function (h) { return h.temperature_c; });
    var vibSeries = m.sensor_history_72h.map(function (h) { return h.vibration_mm_s; });

    var html = ""
      + '<div class="detail-header">'
      + '  <div><div class="detail-id">' + m.machine_id + '</div><div class="detail-sub">' + m.line + '</div></div>'
      + '  <button class="close-btn" id="detail-close" aria-label="Close">\u00D7</button>'
      + '</div>'
      + '<div class="status-band ' + m.flag + '"><span class="status-dot"></span>' + (m.flag === "high_risk" ? "High-risk alert" : m.flag === "watch" ? "Watch condition" : m.flag === "monitor_new_asset" ? "New / limited history" : "Healthy") + '</div>'
      + '<div class="stat-grid">'
      + '  <div><div class="stat-label">Risk score (24h)</div><div class="stat-value">' + (m.risk_score != null ? Math.round(m.risk_score * 100) + '%' : 'n/a') + '</div></div>'
      + '  <div><div class="stat-label">Run-hours since maintenance</div><div class="stat-value">' + fmt(m.run_hours_since_maintenance) + 'h</div></div>'
      + '  <div><div class="stat-label">Temperature</div><div class="stat-value">' + fmt(m.current_temperature_c) + '\u00B0C</div></div>'
      + '  <div><div class="stat-label">Temp. z-score</div><div class="stat-value">' + (m.temp_zscore != null ? m.temp_zscore.toFixed(2) : 'n/a') + '</div></div>'
      + '  <div><div class="stat-label">Vibration</div><div class="stat-value">' + fmt(m.current_vibration_mm_s) + 'mm/s</div></div>'
      + '  <div><div class="stat-label">Vibration z-score</div><div class="stat-value">' + (m.vib_zscore != null ? m.vib_zscore.toFixed(2) : 'n/a') + '</div></div>'
      + '</div>'
      + '<div class="method-line">' + m.confidence + ' confidence \u2014 ' + m.method
      + (m.total_historical_failures > 0 ? ' \u00B7 ' + m.total_historical_failures + ' past failure(s) on record' : '') + '</div>'
      + '<div class="charts-section">'
      + (riskSeries.length > 1 ? '  <div class="chart-block"><div class="chart-title"><span>Risk score</span><em>last 14d</em></div><canvas id="chart-risk"></canvas></div>' : '')
      + (tempSeries.length > 1 ? '  <div class="chart-block"><div class="chart-title"><span>Temperature</span><em>last ' + tempSeries.length + 'h</em></div><canvas id="chart-temp"></canvas></div>' : '')
      + (vibSeries.length > 1 ? '  <div class="chart-block"><div class="chart-title"><span>Vibration</span><em>last ' + vibSeries.length + 'h</em></div><canvas id="chart-vib"></canvas></div>' : '')
      + '</div>'
      + '<div class="explain-section">'
      + '  <button class="explain-btn" id="explain-btn">Explain this flag</button>'
      + '  <div id="explain-error" class="error-text" style="display:none"></div>'
      + '  <div id="explain-box" class="explanation-box" style="display:none"></div>'
      + '</div>';

    detailPanel.innerHTML = html;
    detailPanel.classList.add("open");
    detailBackdrop.classList.add("open");

    if (riskSeries.length > 1) lineChart("chart-risk", riskSeries, "#B5541C");
    if (tempSeries.length > 1) lineChart("chart-temp", tempSeries, "#B23A2E");
    if (vibSeries.length > 1) lineChart("chart-vib", vibSeries, "#5B5F55");

    document.getElementById("detail-close").addEventListener("click", closeDetail);
    detailBackdrop.addEventListener("click", closeDetail, { once: true });

    document.getElementById("explain-btn").addEventListener("click", function () {
      var btn = this;
      var errBox = document.getElementById("explain-error");
      var box = document.getElementById("explain-box");
      btn.disabled = true;
      btn.textContent = "Reading the sensors\u2026";
      errBox.style.display = "none";
      box.style.display = "none";
      fetch("/api/explain", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ machine_id: m.machine_id }),
      })
        .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
        .then(function (res) {
          if (!res.ok) throw new Error(res.data.error || "Failed to generate explanation");
          box.textContent = res.data.explanation;
          box.style.display = "";
        })
        .catch(function (e) {
          errBox.textContent = e.message;
          errBox.style.display = "";
        })
        .finally(function () {
          btn.disabled = false;
          btn.textContent = "Explain this flag";
        });
    });
  }

  function highlightSelectedRow() {
    var rows = tableBody.querySelectorAll("tr.row");
    for (var i = 0; i < rows.length; i++) {
      rows[i].classList.toggle("selected", rows[i].getAttribute("data-machine-id") === selectedId);
    }
  }

  tableBody.addEventListener("click", function (e) {
    var row = e.target.closest("tr.row");
    if (!row) return;
    selectedId = row.getAttribute("data-machine-id");
    highlightSelectedRow();
    renderDetail(machineById[selectedId]);
  });

  var qaForm = document.getElementById("qa-form");
  var qaInput = document.getElementById("qa-input");
  var qaSubmit = document.getElementById("qa-submit");
  var qaError = document.getElementById("qa-error");
  var qaAnswer = document.getElementById("qa-answer");

  qaForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var question = qaInput.value.trim();
    if (!question) return;
    qaSubmit.disabled = true;
    qaSubmit.textContent = "Thinking\u2026";
    qaError.style.display = "none";
    qaAnswer.style.display = "none";
    fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question }),
    })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Failed to get an answer");
        var html = res.data.answer;
        if (res.data.considered_machines && res.data.considered_machines.length) {
          html += '<div class="qa-sources">Based on:';
          res.data.considered_machines.forEach(function (mid) {
            html += '<span class="qa-source-tag">' + mid + '</span>';
          });
          html += '</div>';
        }
        qaAnswer.innerHTML = html;
        qaAnswer.style.display = "";
      })
      .catch(function (e) {
        qaError.textContent = e.message;
        qaError.style.display = "";
      })
      .finally(function () {
        qaSubmit.disabled = false;
        qaSubmit.textContent = "Ask";
      });
  });
})();
