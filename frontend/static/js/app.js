// Dashboard orchestrator: wires the map, charts, websocket feed, and panels.
(async function () {
  const meta = await fetch("/api/meta").then((r) => r.json());
  ChennaiMap.init(meta.center);
  ChennaiMap.initRoads(meta.roads);
  RoadChart.init("road-chart");

  const roadSelect = document.getElementById("road-select");
  meta.roads
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name))
    .forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.id;
      opt.textContent = r.name;
      roadSelect.appendChild(opt);
    });

  const statusEl = document.getElementById("conn-status");
  const clockEl = document.getElementById("clock");
  const statAvg = document.getElementById("stat-avg");
  const statHotspots = document.getElementById("stat-hotspots");
  const statIncidents = document.getElementById("stat-incidents");
  const statTick = document.getElementById("stat-tick");
  const hotspotList = document.getElementById("hotspot-list");
  const alertList = document.getElementById("alert-list");
  const forecastNote = document.getElementById("forecast-note");
  const rainBtn = document.getElementById("rain-toggle");
  const heatBtn = document.getElementById("heat-toggle");
  const diagEps = document.getElementById("diag-eps");
  const diagMinPts = document.getElementById("diag-minpts");
  const diagPoints = document.getElementById("diag-points");
  const diagNoise = document.getElementById("diag-noise");
  const diagCohesion = document.getElementById("diag-cohesion");

  function severityBadge(sev) {
    return `<span class="badge badge-${sev}">${sev}</span>`;
  }

  function renderHotspots(hotspots) {
    hotspotList.innerHTML = "";
    if (!hotspots.length) {
      hotspotList.innerHTML = `<li class="muted">No active hotspots right now.</li>`;
      return;
    }
    hotspots.slice(0, 8).forEach((h) => {
      const li = document.createElement("li");
      li.innerHTML = `${severityBadge(h.severity)} ${h.roads[0] || "Unnamed road"}<br>
        <span class="muted">${(h.mean_congestion * 100).toFixed(0)}% congestion &middot; ${h.point_count} pts &middot; ${h.radius_km.toFixed(2)} km radius</span>`;
      hotspotList.appendChild(li);
    });
  }

  function renderAlerts(newAlerts) {
    newAlerts.forEach((a) => {
      const li = document.createElement("li");
      const t = new Date(a.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      li.innerHTML = `${severityBadge(a.severity)} ${a.message}<br><span class="muted">${t}</span>`;
      alertList.prepend(li);
    });
    while (alertList.children.length > 25) alertList.removeChild(alertList.lastChild);
  }

  function updateDiagnostics(diag) {
    if (!diag) return;
    diagEps.textContent = diag.eps_km_used != null ? `${diag.eps_km_used} km` : "–";
    diagMinPts.textContent = diag.min_samples ?? "–";
    diagPoints.textContent = diag.points_considered ?? "–";
    diagNoise.textContent = diag.noise_ratio != null ? `${(diag.noise_ratio * 100).toFixed(0)}%` : "–";
    diagCohesion.textContent = diag.mean_cohesion_km != null ? `${diag.mean_cohesion_km} km` : "–";
  }

  function updateStats(data) {
    const pings = data.pings || [];
    const avg = pings.length ? pings.reduce((s, p) => s + p.congestion, 0) / pings.length : 0;
    statAvg.textContent = `${(avg * 100).toFixed(0)}%`;
    statHotspots.textContent = (data.hotspots || []).length;
    statIncidents.textContent = (data.incidents || []).length;
    statTick.textContent = data.tick ?? 0;
  }

  LiveFeed.onStatus((s) => {
    statusEl.className = `status-pill status-${s}`;
    statusEl.textContent = s === "live" ? "live" : s === "down" ? "reconnecting…" : "connecting…";
  });

  LiveFeed.onMessage((data) => {
    if (data.type !== "snapshot") return;
    ChennaiMap.updateRoadTraffic(data.pings);
    ChennaiMap.updatePings(data.pings);
    ChennaiMap.updateHotspots(data.hotspots);
    ChennaiMap.updateIncidents(data.incidents);
    renderHotspots(data.hotspots);
    if (data.alerts && data.alerts.length) renderAlerts(data.alerts);
    updateStats(data);
    updateDiagnostics(data.diagnostics);
    rainBtn.classList.toggle("active", !!data.rain_active);
  });

  LiveFeed.connect();

  heatBtn.addEventListener("click", () => {
    const enabled = ChennaiMap.toggleHeat();
    heatBtn.classList.toggle("active", enabled);
  });

  async function refreshRoadChart() {
    const roadId = roadSelect.value;
    if (!roadId) return;
    const [hist, fc] = await Promise.all([
      fetch(`/api/history/${roadId}?minutes=30`).then((r) => r.json()),
      fetch(`/api/forecast/${roadId}`).then((r) => r.json()),
    ]);
    RoadChart.update(hist.points, fc);
    forecastNote.textContent = fc.predictions && fc.predictions.length
      ? `Trend: ${fc.trend}. Shaded band = forecast uncertainty (widens with horizon).`
      : "Collecting history for this road — check back in ~20s as the feed persists snapshots.";
  }

  roadSelect.addEventListener("change", refreshRoadChart);
  if (roadSelect.options.length) {
    roadSelect.selectedIndex = 0;
    refreshRoadChart();
  }
  setInterval(refreshRoadChart, 15000);

  rainBtn.addEventListener("click", async () => {
    const res = await fetch("/api/rain/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }).then((r) => r.json());
    rainBtn.classList.toggle("active", res.rain_active);
  });

  function tickClock() {
    clockEl.textContent = new Date().toLocaleTimeString([], { hour12: false });
  }
  tickClock();
  setInterval(tickClock, 1000);
})();
