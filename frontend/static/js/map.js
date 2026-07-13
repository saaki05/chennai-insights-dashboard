// Leaflet + OpenStreetMap map layer management (free, no API key required).
const ChennaiMap = (() => {
  let map, canvasRenderer;
  let pointLayer, hotspotLayer, incidentLayer, heatLayer;
  let heatEnabled = false;

  const CONGESTION_COLORS = [
    { max: 0.35, color: "#2ecc71" },
    { max: 0.55, color: "#f1c40f" },
    { max: 0.75, color: "#e67e22" },
    { max: 1.01, color: "#e74c3c" },
  ];

  const INCIDENT_EMOJI = {
    accident: "🚧",
    waterlogging: "💧",
    roadwork: "🛠️",
    event: "🎉",
  };

  function colorFor(congestion) {
    return CONGESTION_COLORS.find((c) => congestion <= c.max).color;
  }

  function init(center, zoom = 12) {
    map = L.map("map", { preferCanvas: true }).setView([center.lat, center.lon], zoom);
    canvasRenderer = L.canvas({ padding: 0.5 });

    // CartoDB "Dark Matter" basemap: free, no API key, no signup — chosen
    // over default OSM tiles because it reads much better under a dark
    // dashboard UI (muted basemap lets the traffic overlay stay the focal
    // point) while still being backed by OpenStreetMap data underneath.
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: "abcd",
      maxZoom: 19,
    }).addTo(map);

    pointLayer = L.layerGroup().addTo(map);
    hotspotLayer = L.layerGroup().addTo(map);
    incidentLayer = L.layerGroup().addTo(map);
    heatLayer = L.heatLayer([], { radius: 22, blur: 18, maxZoom: 15 });

    return map;
  }

  function toggleHeat(force) {
    heatEnabled = force !== undefined ? force : !heatEnabled;
    if (heatEnabled) {
      map.addLayer(heatLayer);
      map.removeLayer(pointLayer);
    } else {
      map.removeLayer(heatLayer);
      map.addLayer(pointLayer);
    }
    return heatEnabled;
  }

  function updatePings(pings) {
    pointLayer.clearLayers();
    const heatPoints = [];
    pings.forEach((p) => {
      const severe = p.congestion >= 0.75;
      const color = colorFor(p.congestion);

      if (severe) {
        // Soft outer halo so severe points read as "hot" at a glance even
        // when zoomed out, without needing a legend lookup.
        pointLayer.addLayer(L.circleMarker([p.lat, p.lon], {
          renderer: canvasRenderer,
          radius: 8,
          color,
          weight: 0,
          fillColor: color,
          fillOpacity: 0.18,
        }));
      }

      const marker = L.circleMarker([p.lat, p.lon], {
        renderer: canvasRenderer,
        radius: severe ? 4 : 3.2,
        color: severe ? "#fff" : color,
        weight: severe ? 1 : 0,
        fillColor: color,
        fillOpacity: 0.92,
      });
      marker.bindTooltip(
        `<b>${p.road_name}</b><br>Congestion: ${(p.congestion * 100).toFixed(0)}%<br>Speed: ${p.speed_kmph} km/h`,
        { sticky: true }
      );
      pointLayer.addLayer(marker);
      heatPoints.push([p.lat, p.lon, p.congestion]);
    });
    heatLayer.setLatLngs(heatPoints);
  }

  const SEVERITY_COLOR = { severe: "#e74c3c", high: "#e67e22", moderate: "#f1c40f", low: "#2ecc71" };

  function updateHotspots(hotspots) {
    hotspotLayer.clearLayers();
    hotspots.forEach((h) => {
      const color = SEVERITY_COLOR[h.severity] || "#5b9dff";
      const radiusM = Math.max(h.radius_km, 0.15) * 1000;

      if (h.severity === "severe" || h.severity === "high") {
        hotspotLayer.addLayer(L.circle([h.centroid.lat, h.centroid.lon], {
          radius: radiusM * 1.5,
          color,
          weight: 1,
          dashArray: "3 5",
          fillOpacity: 0.03,
        }));
      }

      const circle = L.circle([h.centroid.lat, h.centroid.lon], {
        radius: radiusM,
        color,
        weight: 2.25,
        fillColor: color,
        fillOpacity: 0.14,
      });
      circle.bindPopup(
        `<div style="min-width:180px">
          <div style="font-weight:700;color:${color};margin-bottom:4px;">${h.severity.toUpperCase()} HOTSPOT</div>
          <div style="color:#8492b0;margin-bottom:8px;">${h.roads.join(", ")}</div>
          <div style="display:flex;justify-content:space-between;"><span>Mean congestion</span><b>${(h.mean_congestion * 100).toFixed(0)}%</b></div>
          <div style="display:flex;justify-content:space-between;"><span>Points in cluster</span><b>${h.point_count}</b></div>
          <div style="display:flex;justify-content:space-between;"><span>Radius</span><b>${h.radius_km.toFixed(2)} km</b></div>
          <div style="display:flex;justify-content:space-between;"><span>Cohesion</span><b>${h.cohesion_km.toFixed(2)} km</b></div>
        </div>`
      );
      hotspotLayer.addLayer(circle);
    });
  }

  function updateIncidents(incidents) {
    incidentLayer.clearLayers();
    incidents.forEach((inc) => {
      const icon = L.divIcon({
        className: "incident-icon",
        html: `<div style="font-size:18px;">${INCIDENT_EMOJI[inc.kind] || "⚠️"}</div>`,
        iconSize: [22, 22],
      });
      const marker = L.marker([inc.lat, inc.lon], { icon });
      marker.bindPopup(
        `<b>${inc.kind}</b> on ${inc.road_name}<br>Severity: ${(inc.severity * 100).toFixed(0)}%<br>Clears in ~${inc.expires_in_s}s`
      );
      incidentLayer.addLayer(marker);
    });
  }

  return { init, updatePings, updateHotspots, updateIncidents, toggleHeat };
})();
