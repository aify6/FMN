(function () {
  "use strict";

  const SKUS = JSON.parse(document.getElementById("sku-data").textContent);
  const skuById = {};
  for (let i = 0; i < SKUS.length; i += 1) {
    skuById[SKUS[i].sku_id] = SKUS[i];
  }

  let activeFilter = "all";
  let selectedId = null;
  const charts = {};

  const tableBody = document.querySelector("#sku-table tbody");
  const emptyState = document.getElementById("empty-state");
  const detailPanel = document.getElementById("detail-panel");
  const detailBackdrop = document.getElementById("detail-backdrop");
  const tiles = document.querySelectorAll(".stat-tile");

  function applyFilter() {
    const rows = tableBody.querySelectorAll("tr.row");
    let visibleCount = 0;

    rows.forEach((row) => {
      const show = activeFilter === "all" || row.getAttribute("data-flag") === activeFilter;
      row.style.display = show ? "" : "none";
      if (show) visibleCount += 1;
    });

    emptyState.style.display = visibleCount === 0 ? "" : "none";
  }

  tiles.forEach((tile) => {
    tile.addEventListener("click", function () {
      const filter = this.getAttribute("data-filter");
      activeFilter = activeFilter === filter ? "all" : filter;

      tiles.forEach((item) => item.classList.remove("active"));
      if (activeFilter !== "all") this.classList.add("active");

      applyFilter();
    });
  });

  function fmt(value) {
    return value === null || value === undefined ? "—" : value;
  }

  function destroyCharts() {
    Object.keys(charts).forEach((key) => {
      charts[key].destroy();
      delete charts[key];
    });
  }

  function lineChart(canvasId, labels, values, color) {
    if (typeof Chart === "undefined") return;

    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    ctx.style.height = "120px";
    ctx.style.width = "100%";

    charts[canvasId] = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [{
          data: values,
          borderColor: color,
          backgroundColor: `${color}28`,
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
    const soldLabels = sku.history.map((item) => item.date);
    const soldValues = sku.history.map((item) => item.units_sold);
    const stockValues = sku.history.map((item) => item.closing_stock);

    const html = ""
      + '<div class="detail-header">'
      + `  <div><div class="detail-id">${sku.sku_id}</div><div class="detail-sub">${sku.category}</div></div>`
      + '  <button class="close-btn" id="detail-close" aria-label="Close">×</button>'
      + `</div>`
      + `<div class="status-band ${sku.flag}"><span class="status-dot"></span>${sku.flag === "stockout_risk" ? "Risk of stock-out" : sku.flag === "overstock_risk" ? "Overstock signal" : "Healthy"}</div>`
      + '<div class="stat-grid">'
      + `  <div><div class="stat-label">Closing stock</div><div class="stat-value">${fmt(sku.closing_stock)} units</div></div>`
      + `  <div><div class="stat-label">Forecast demand</div><div class="stat-value">${fmt(sku.forecast_daily_demand)}/day</div></div>`
      + `  <div><div class="stat-label">Lead time</div><div class="stat-value">${fmt(sku.lead_time_days)} days${sku.lead_time_reliable ? "" : " *"}</div></div>`
      + `  <div><div class="stat-label">Cover</div><div class="stat-value">${sku.days_of_cover != null ? `${sku.days_of_cover} days` : "—"}</div></div>`
      + `  <div><div class="stat-label">Demand over lead time</div><div class="stat-value">${fmt(sku.projected_demand_over_lead_time)} units</div></div>`
      + `  <div><div class="stat-label">Stock vs. lead demand</div><div class="stat-value">${sku.stock_to_lead_demand_ratio != null ? `${sku.stock_to_lead_demand_ratio}×` : "—"}</div></div>`
      + '</div>'
      + `<div class="method-line">${sku.confidence} confidence — ${sku.forecast_method}${sku.lead_time_reliable ? "" : " · * lead time has been inconsistent in the source data"}</div>`
      + '<div class="charts-section">'
      + `  <div class="chart-block"><div class="chart-title"><span>Units sold</span><em>last ${soldValues.length}d</em></div><canvas id="chart-sold"></canvas></div>`
      + `  <div class="chart-block"><div class="chart-title"><span>Closing stock</span><em>last ${stockValues.length}d</em></div><canvas id="chart-stock"></canvas></div>`
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
      const btn = this;
      const errBox = document.getElementById("explain-error");
      const box = document.getElementById("explain-box");

      btn.disabled = true;
      btn.textContent = "Reading the numbers…";
      errBox.style.display = "none";
      box.style.display = "none";

      fetch("/api/explain", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sku_id: sku.sku_id }),
      })
        .then((response) => response.json().then((data) => ({ ok: response.ok, data })))
        .then((res) => {
          if (!res.ok) throw new Error(res.data.error || "Failed to generate explanation");
          box.textContent = res.data.explanation;
          box.style.display = "";
        })
        .catch((error) => {
          errBox.textContent = error.message;
          errBox.style.display = "";
        })
        .finally(() => {
          btn.disabled = false;
          btn.textContent = "Explain this flag";
        });
    });
  }

  function highlightSelectedRow() {
    const rows = tableBody.querySelectorAll("tr.row");
    rows.forEach((row) => {
      row.classList.toggle("selected", row.getAttribute("data-sku-id") === selectedId);
    });
  }

  tableBody.addEventListener("click", (event) => {
    const row = event.target.closest("tr.row");
    if (!row) return;

    selectedId = row.getAttribute("data-sku-id");
    highlightSelectedRow();
    renderDetail(skuById[selectedId]);
  });

  const qaForm = document.getElementById("qa-form");
  const qaInput = document.getElementById("qa-input");
  const qaSubmit = document.getElementById("qa-submit");
  const qaError = document.getElementById("qa-error");
  const qaAnswer = document.getElementById("qa-answer");

  qaForm.addEventListener("submit", (event) => {
    event.preventDefault();

    const question = qaInput.value.trim();
    if (!question) return;

    qaSubmit.disabled = true;
    qaSubmit.textContent = "Thinking…";
    qaError.style.display = "none";
    qaAnswer.style.display = "none";

    fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    })
      .then((response) => response.json().then((data) => ({ ok: response.ok, data })))
      .then((res) => {
        if (!res.ok) throw new Error(res.data.error || "Failed to get an answer");

        let html = res.data.answer;
        if (res.data.considered_skus && res.data.considered_skus.length) {
          html += '<div class="qa-sources">Based on:';
          res.data.considered_skus.forEach((skuId) => {
            html += `<span class="qa-source-tag">${skuId}</span>`;
          });
          html += '</div>';
        }

        qaAnswer.innerHTML = html;
        qaAnswer.style.display = "";
      })
      .catch((error) => {
        qaError.textContent = error.message;
        qaError.style.display = "";
      })
      .finally(() => {
        qaSubmit.disabled = false;
        qaSubmit.textContent = "Ask";
      });
  });
})();
