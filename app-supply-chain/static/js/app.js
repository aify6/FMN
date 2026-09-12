(function () {
  "use strict";

  var SKUS = JSON.parse(document.getElementById("sku-data").textContent);
  var skuById = {};
  for (var i = 0; i < SKUS.length; i++) skuById[SKUS[i].sku_id] = SKUS[i];

  var activeFilter = "all";
  var selectedId = null;
  var charts = {};

  var tableBody = document.querySelector("#sku-table tbody");
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

  function fmt(n) {
    if (n === null || n === undefined) return "—";
    return n;
  }

  function destroyCharts() {
    Object.keys(charts).forEach(function (k) {
      charts[k].destroy();
    });
    charts = {};
  }

  function lineChart(canvasId, labels, values, color) {
    var ctx = document.getElementById(canvasId);
    if (!ctx) return;
    ctx.style.height = "120px";
    ctx.style.width = "100%";
    charts[canvasId] = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [{
          data: values,
          borderColor: color,
          backgroundColor: color + "28",
          fill: true,
          cubicInterpolationMode: "monotone",
          tension: 0.35,
          pointRadius: 1,
          pointHoverRadius: 4,
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        resizeDelay: 0,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { enabled: true },
        },
        layout: { padding: 0 },
        scales: {
          x: {
            display: true,
            grid: { display: false },
            ticks: {
              maxTicksLimit: 6,
              color: "#53707d",
              maxRotation: 0,
            },
          },
          y: {
            display: true,
            grid: { color: "rgba(83,112,125,0.12)" },
            ticks: { color: "#53707d" },
          },
        },
      },
    });
    charts[canvasId].resize();
  }

  function closeDetail() {
    detailPanel.classList.remove("open");
    detailBackdrop.classList.remove("open");
    selectedId = null;
    highlightSelectedRow();
  }

  function renderDetail(sku) {
    var soldLabels = sku.history.map(function (h) { return h.date; });
    var soldValues = sku.history.map(function (h) { return h.units_sold; });
    var stockValues = sku.history.map(function (h) { return h.closing_stock; });

    var html = ""
      + '<div class="detail-header">'
      + '  <div><div class="detail-id">' + sku.sku_id + '</div><div class="detail-sub">' + sku.category + '</div></div>'
      + '  <button class="close-btn" id="detail-close" aria-label="Close">\u00D7</button>'
      + '</div>'
      + '<div class="status-band ' + sku.flag + '"><span class="status-dot"></span>' + (sku.flag === "stockout_risk" ? "Risk of stock-out" : sku.flag === "overstock_risk" ? "Overstock signal" : "Healthy") + '</div>'
      + '<div class="stat-grid">'
      + '  <div><div class="stat-label">Closing stock</div><div class="stat-value">' + fmt(sku.closing_stock) + ' units</div></div>'
      + '  <div><div class="stat-label">Forecast demand</div><div class="stat-value">' + fmt(sku.forecast_daily_demand) + '/day</div></div>'
      + '  <div><div class="stat-label">Lead time</div><div class="stat-value">' + fmt(sku.lead_time_days) + ' days' + (sku.lead_time_reliable ? '' : ' *') + '</div></div>'
      + '  <div><div class="stat-label">Cover</div><div class="stat-value">' + (sku.days_of_cover != null ? sku.days_of_cover + ' days' : '\u2014') + '</div></div>'
      + '  <div><div class="stat-label">Demand over lead time</div><div class="stat-value">' + fmt(sku.projected_demand_over_lead_time) + ' units</div></div>'
      + '  <div><div class="stat-label">Stock vs. lead demand</div><div class="stat-value">' + (sku.stock_to_lead_demand_ratio != null ? sku.stock_to_lead_demand_ratio + '\u00D7' : '\u2014') + '</div></div>'
      + '</div>'
      + '<div class="method-line">' + sku.confidence + ' confidence \u2014 ' + sku.forecast_method
      + (sku.lead_time_reliable ? '' : ' \u00B7 * lead time has been inconsistent in the source data') + '</div>'
      + '<div class="charts-section">'
      + '  <div class="chart-block"><div class="chart-title"><span>Units sold</span><em>last ' + soldValues.length + 'd</em></div><canvas id="chart-sold"></canvas></div>'
      + '  <div class="chart-block"><div class="chart-title"><span>Closing stock</span><em>last ' + stockValues.length + 'd</em></div><canvas id="chart-stock"></canvas></div>'
      + '</div>'
      + '<div class="explain-section">'
      + '  <button class="explain-btn" id="explain-btn">Explain this flag</button>'
      + '  <div id="explain-error" class="error-text" style="display:none"></div>'
      + '  <div id="explain-box" class="explanation-box" style="display:none"></div>'
      + '</div>';

    detailPanel.innerHTML = html;
    detailPanel.classList.add("open");
    detailBackdrop.classList.add("open");

    destroyCharts();
    if (soldValues.length > 1) {
      lineChart("chart-sold", soldLabels, soldValues, "#0E6E5C");
      lineChart("chart-stock", soldLabels, stockValues, "#5B5F55");
    }

    document.getElementById("detail-close").addEventListener("click", closeDetail);
    detailBackdrop.addEventListener("click", closeDetail, { once: true });

    document.getElementById("explain-btn").addEventListener("click", function () {
      var btn = this;
      var errBox = document.getElementById("explain-error");
      var box = document.getElementById("explain-box");
      btn.disabled = true;
      btn.textContent = "Reading the numbers\u2026";
      errBox.style.display = "none";
      box.style.display = "none";
      fetch("/api/explain", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sku_id: sku.sku_id }),
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
      rows[i].classList.toggle("selected", rows[i].getAttribute("data-sku-id") === selectedId);
    }
  }

  tableBody.addEventListener("click", function (e) {
    var row = e.target.closest("tr.row");
    if (!row) return;
    selectedId = row.getAttribute("data-sku-id");
    highlightSelectedRow();
    renderDetail(skuById[selectedId]);
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
        if (res.data.considered_skus && res.data.considered_skus.length) {
          html += '<div class="qa-sources">Based on:';
          res.data.considered_skus.forEach(function (s) {
            html += '<span class="qa-source-tag">' + s + '</span>';
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
