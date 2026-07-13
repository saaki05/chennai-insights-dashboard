// Road-level historical + forecast congestion chart (Chart.js).
const RoadChart = (() => {
  let chart;

  function init(canvasId) {
    const ctx = document.getElementById(canvasId).getContext("2d");
    chart = new Chart(ctx, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Confidence band",
            data: [],
            borderColor: "transparent",
            backgroundColor: "rgba(251,191,36,0.12)",
            pointRadius: 0,
            fill: "+1",
            order: 3,
          },
          {
            label: "_lower",
            data: [],
            borderColor: "transparent",
            pointRadius: 0,
            fill: false,
            order: 4,
          },
          {
            label: "Observed",
            data: [],
            borderColor: "#5b9dff",
            backgroundColor: "rgba(91,157,255,0.16)",
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 2,
            fill: true,
            order: 1,
          },
          {
            label: "Forecast",
            data: [],
            borderColor: "#fbbf24",
            borderDash: [5, 4],
            borderWidth: 2,
            pointRadius: 2.5,
            pointBackgroundColor: "#fbbf24",
            fill: false,
            order: 0,
          },
        ],
      },
      options: {
        responsive: true,
        animation: false,
        interaction: { intersect: false, mode: "index" },
        scales: {
          y: {
            min: 0,
            max: 1,
            ticks: { color: "#8492b0", callback: (v) => `${Math.round(v * 100)}%`, font: { size: 10.5 } },
            grid: { color: "rgba(255,255,255,0.06)" },
          },
          x: { ticks: { color: "#8492b0", maxTicksLimit: 6, font: { size: 10 } }, grid: { display: false } },
        },
        plugins: {
          legend: {
            labels: {
              color: "#eef2fb",
              boxWidth: 10,
              font: { size: 10.5 },
              filter: (item) => !item.text.startsWith("_"),
            },
          },
        },
      },
    });
    return chart;
  }

  function fmtTime(tsSeconds) {
    return new Date(tsSeconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function update(historyPoints, forecast) {
    const labels = historyPoints.map((p) => fmtTime(p.ts));
    const observed = historyPoints.map((p) => p.mean_congestion);
    const upper = new Array(observed.length).fill(null);
    const lower = new Array(observed.length).fill(null);
    const forecastData = new Array(observed.length).fill(null);

    if (forecast && forecast.predictions && forecast.predictions.length && historyPoints.length) {
      const lastTs = historyPoints[historyPoints.length - 1].ts;
      const lastObs = observed[observed.length - 1];
      forecastData[forecastData.length - 1] = lastObs;
      upper[upper.length - 1] = lastObs;
      lower[lower.length - 1] = lastObs;

      forecast.predictions.forEach((pred) => {
        labels.push(fmtTime(lastTs + pred.horizon_s));
        forecastData.push(pred.predicted_congestion);
        upper.push(pred.upper);
        lower.push(pred.lower);
        observed.push(null);
      });
    }

    chart.data.labels = labels;
    chart.data.datasets[0].data = upper;
    chart.data.datasets[1].data = lower;
    chart.data.datasets[2].data = observed;
    chart.data.datasets[3].data = forecastData;
    chart.update();
  }

  return { init, update };
})();
