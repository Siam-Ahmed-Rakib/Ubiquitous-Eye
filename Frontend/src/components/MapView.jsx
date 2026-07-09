import { useEffect, useRef, useState, useCallback } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet-draw/dist/leaflet.draw.css";
import "leaflet-draw";
import "./MapView.css";

const CFG = {
  deforestation: {
    label: "Deforestation",
    color: "#22c55e",
    glow: "rgba(34,197,94,.35)",
    badge: "badge-green",
    icon: "🌳",
    desc: "Forest cover loss & vegetation degradation",
  },
  land_encroachment: {
    label: "Land Encroachment",
    color: "#f59e0b",
    glow: "rgba(245,158,11,.35)",
    badge: "badge-amber",
    icon: "🏗️",
    desc: "Illegal land use & unauthorized settlements",
  },
  river_erosion: {
    label: "River Erosion",
    color: "#3b82f6",
    glow: "rgba(59,130,246,.35)",
    badge: "badge-blue",
    icon: "🌊",
    desc: "Riverbank change & channel migration",
  },
};

const TILES = [
  {
    id: "osm",
    label: "Streets",
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attr: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  },
  {
    id: "satellite",
    label: "Satellite",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr: "Tiles &copy; Esri",
    maxZoom: 19,
  },
  {
    id: "topo",
    label: "Topo",
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    attr: "&copy; OpenTopoMap",
    maxZoom: 17,
  },
  {
    id: "dark",
    label: "Dark",
    url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    attr: "&copy; CARTO",
    maxZoom: 19,
  },
];

const BD_CENTER = [23.685, 90.3563];
const BD_ZOOM = 7;
const BD_BOUNDS = L.latLngBounds(L.latLng(20.5, 87.9), L.latLng(26.8, 92.8));
const BD_BORDER = [
  [88.08, 22.88],
  [88.14, 23.63],
  [88.74, 23.24],
  [88.56, 24.05],
  [88.96, 24.26],
  [88.33, 24.87],
  [88.08, 25.19],
  [88.51, 25.68],
  [89.01, 25.93],
  [89.37, 26.02],
  [89.86, 26.02],
  [90.37, 26.09],
  [90.56, 26.46],
  [91.28, 26.5],
  [91.91, 26.18],
  [92.05, 26.0],
  [92.33, 25.09],
  [91.63, 25.02],
  [91.16, 24.09],
  [91.65, 23.62],
  [91.99, 23.7],
  [92.04, 23.01],
  [91.71, 22.52],
  [91.33, 23.1],
  [90.99, 23.16],
  [90.47, 21.97],
  [89.84, 21.43],
  [89.09, 21.74],
  [88.08, 21.76],
  [88.08, 22.88],
];

const fmtCoord = (v, ax) =>
  Math.abs(v).toFixed(5) +
  "° " +
  (ax === "lat" ? (v >= 0 ? "N" : "S") : v >= 0 ? "E" : "W");

const fmtBytes = (b) =>
  b < 1024
    ? b + " B"
    : b < 1048576
      ? (b / 1024).toFixed(1) + " KB"
      : (b / 1048576).toFixed(2) + " MB";

export default function MapView({ analysisType, onBack }) {
  const mapElRef = useRef(null);
  const mapRef = useRef(null);
  const tileRef = useRef(null);
  const drawnRef = useRef(null);
  const dcRef = useRef(null);
  const rectDrawerRef = useRef(null);

  const [activeTile, setActiveTile] = useState("satellite");
  const [sel, setSel] = useState(null);
  const [mousePos, setMousePos] = useState(null);
  const [zoom, setZoom] = useState(BD_ZOOM);
  const [drawing, setDrawing] = useState(false);
  const [ready, setReady] = useState(false);
  const [sidebar, setSidebar] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [result, setResult] = useState(null);
  const [toast, setToast] = useState(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisResult, setAnalysisResult] = useState(null);

  // Date state for SCL downloads
  const [oldYear, setOldYear] = useState(new Date().getFullYear() - 1);
  const [oldMonth, setOldMonth] = useState(1);
  const [newYear, setNewYear] = useState(new Date().getFullYear());
  const [newMonth, setNewMonth] = useState(new Date().getMonth() + 1);

  const cfg = CFG[analysisType] || CFG.deforestation;

  /* ── Toast ───────────────────────────────────────────────── */
  const pushToast = useCallback((msg, type = "info", ms = 3500) => {
    setToast({ msg, type, key: Date.now() });
    setTimeout(() => setToast(null), ms);
  }, []);

  const overlayRef = useRef(null);

  const sendToBackend = useCallback(async (layer) => {
    if (analyzing) return;

    let polygon = [];
    if (layer && typeof layer.getLatLngs === "function") {
      const latLngs = layer.getLatLngs();
      if (Array.isArray(latLngs) && latLngs[0]) {
        polygon = latLngs[0].map((pt) => [pt.lng, pt.lat]);
      }
    }

    if (!polygon.length && drawnRef.current) {
      const layers = drawnRef.current.getLayers();
      if (layers.length > 0 && typeof layers[0].getLatLngs === "function") {
        const latLngs = layers[0].getLatLngs();
        if (Array.isArray(latLngs) && latLngs[0]) {
          polygon = latLngs[0].map((pt) => [pt.lng, pt.lat]);
        }
      }
    }

    if (polygon.length < 3) {
      pushToast("Invalid polygon. Draw at least 3 points.", "error", 4000);
      return;
    }

    setAnalyzing(true);
    setAnalysisResult(null);

    // Clear previous overlay
    if (overlayRef.current && mapRef.current) {
      mapRef.current.removeLayer(overlayRef.current);
      overlayRef.current = null;
    }

    pushToast("Running full analysis pipeline...", "info", 6000);

    try {
      const backendUrl = (import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "");
      const endpoint = backendUrl
        ? `${backendUrl}/api/sentinel/analyze`
        : "/api/sentinel/analyze";

      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          polygon,
          oldYear,
          oldMonth,
          newYear,
          newMonth,
        }),
      });

      const data = await res.json();
      if (!res.ok || data.status !== "success") {
        throw new Error(data.message || `Backend error: ${res.status}`);
      }

      setAnalysisResult({
        stats: data.stats,
        changes: data.changes,
        message: data.message,
        ts: new Date().toISOString(),
      });

      // Render overlay on map
      if (data.changes && data.changes.length > 0 && mapRef.current) {
        const overlayGroup = L.layerGroup();
        data.changes.forEach((pt) => {
          const color = pt.mask === 1 ? "#ff0000" : "#ff8c00";
          const fillColor = pt.mask === 1 ? "rgba(255,0,0,0.5)" : "rgba(255,140,0,0.5)";
          L.circleMarker([pt.Latitude, pt.Longitude], {
            radius: 3,
            color: color,
            weight: 0.5,
            fillColor: fillColor,
            fillOpacity: 0.6,
          }).addTo(overlayGroup);
        });
        overlayGroup.addTo(mapRef.current);
        overlayRef.current = overlayGroup;

        pushToast(
          `Analysis complete: ${data.stats.deforestation} deforestation, ${data.stats.waterLoss} water loss pixels`,
          "success",
          6000
        );
      } else {
        pushToast("Analysis complete: no changes detected", "info", 5000);
      }
    } catch (err) {
      console.error("Analysis error:", err);
      pushToast(`Request failed: ${err.message}`, "error", 5000);
    } finally {
      setAnalyzing(false);
    }
  }, [analyzing, pushToast, oldYear, oldMonth, newYear, newMonth]);

  /* ── Init Leaflet map ────────────────────────────────────── */
  useEffect(() => {
    if (!mapElRef.current) return;
    if (mapRef.current) return; // Already initialized

    let safetyTimer = null;

    const initMap = () => {
      try {
        console.log("🗺️ Initializing Leaflet map...");
        
        // Clean up DOM
        if (mapElRef.current._leaflet_id !== undefined) {
          delete mapElRef.current._leaflet_id;
        }
        while (mapElRef.current.firstChild) {
          mapElRef.current.removeChild(mapElRef.current.firstChild);
        }

        // Fix Leaflet markers
        delete L.Icon.Default.prototype._getIconUrl;
        L.Icon.Default.mergeOptions({
          iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
          iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
          shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
        });

        // Create map
        const map = L.map(mapElRef.current, {
          center: BD_CENTER,
          zoom: BD_ZOOM,
          zoomControl: false,
          maxBounds: BD_BOUNDS.pad(0.6),
          maxBoundsViscosity: 0.8,
        });
        mapRef.current = map;
        console.log("✅ Map created");

        // Add controls
        L.control.zoom({ position: "bottomright" }).addTo(map);

        // Add tiles (Satellite by default)
        const satTile = TILES.find((t) => t.id === "satellite") || TILES[0];
        tileRef.current = L.tileLayer(satTile.url, {
          attribution: satTile.attr,
          maxZoom: satTile.maxZoom,
          crossOrigin: "anonymous",
        }).addTo(map);
        console.log("✅ Tiles added:", satTile.id);

        // Bangladesh border
        L.geoJSON({
          type: "Feature",
          geometry: { type: "Polygon", coordinates: [BD_BORDER] },
        }, {
          style: {
            color: cfg.color,
            weight: 2.5,
            opacity: 0.75,
            fillColor: cfg.color,
            fillOpacity: 0.06,
            dashArray: "7 4",
          },
        }).addTo(map);

        // Label
        L.marker(BD_CENTER, {
          icon: L.divIcon({
            className: "",
            html: `<div style="background:rgba(10,14,26,.85);border:1px solid ${cfg.color};color:#f1f5f9;padding:3px 9px;border-radius:6px;font-size:11px;font-weight:700;white-space:nowrap;backdrop-filter:blur(6px);pointer-events:none;">🇧🇩 Bangladesh</div>`,
            iconAnchor: [62, 12],
          }),
        }).addTo(map);

        // Draw layer & control
        const drawn = new L.FeatureGroup();
        map.addLayer(drawn);
        drawnRef.current = drawn;

        console.log("🔍 Before creating draw control:");
        console.log("   L.Control available:", !!L.Control);
        console.log("   L.Control.Draw available:", !!L.Control.Draw);
        console.log("   L.Draw available:", !!L.Draw);

        try {
          const dc = new L.Control.Draw({
            position: "topleft",
            draw: {
              rectangle: {
                shapeOptions: {
                  color: cfg.color,
                  weight: 2.5,
                  opacity: 0.9,
                  fillColor: cfg.color,
                  fillOpacity: 0.15,
                },
                showArea: true,
                metric: true,
              },
              polygon: false,
              polyline: false,
              circle: false,
              circlemarker: false,
              marker: false,
            },
            edit: { featureGroup: drawn, remove: true, edit: true },
          });
          
          console.log("✅ Draw control created:", !!dc);
          dc.addTo(map);
          console.log("✅ Draw control added to map");
          dcRef.current = dc;
          console.log("🎨 dcRef.current set:", !!dcRef.current);
          console.log("🎨 Draw control initialized:", dc);
          console.log("📋 Draw toolbars:", dc._toolbars);
        } catch (err) {
          console.error("❌ Error creating draw control:", err.message);
          console.error("   Stack:", err.stack);
        }

        // Events
        map.on(L.Draw.Event.CREATED, (e) => {
          console.log("✏️ Region drawn");
          
          // Disable the rectangle drawer to stop drawing mode
          if (rectDrawerRef.current) {
            rectDrawerRef.current.disable();
            console.log("✅ Rectangle drawer disabled");
          }
          
          drawn.clearLayers();
          drawn.addLayer(e.layer);
          setSel({ bounds: e.layer.getBounds() });
          setResult(null);
          setDrawing(false);
          pushToast("✅ Region selected — click Download SCL to start.", "success");
        });

        map.on(L.Draw.Event.DELETED, () => {
          setSel(null);
          setResult(null);
        });

        map.on(L.Draw.Event.EDITSTOP, () => {
          const ls = [];
          drawn.eachLayer((l) => ls.push(l));
          if (ls.length) setSel({ bounds: ls[0].getBounds() });
        });

        map.on(L.Draw.Event.DRAWSTART, () => setDrawing(true));
        map.on(L.Draw.Event.DRAWSTOP, () => setDrawing(false));
        map.on("mousemove", (e) => setMousePos({ lat: e.latlng.lat, lng: e.latlng.lng }));
        map.on("mouseout", () => setMousePos(null));
        map.on("zoomend", () => setZoom(map.getZoom()));

        // Measure & fit
        map.invalidateSize({ animate: false });
        setTimeout(() => {
          if (mapRef.current) {
            mapRef.current.invalidateSize({ animate: false });
            mapRef.current.fitBounds(BD_BOUNDS, { padding: [48, 48] });
            setReady(true);
            console.log("✅ Map ready!");
          }
        }, 100);

        // Redraw again in case layouts shifted
        setTimeout(() => {
          if (mapRef.current) mapRef.current.invalidateSize({ animate: false });
        }, 500);

        // Safety: force ready after 3s even if tiles loading
        safetyTimer = setTimeout(() => {
          setReady(true);
          console.warn("⏱️ Forced map ready (tiles may still loading)");
        }, 3000);

      } catch (err) {
        console.error("❌ Map init error:", err);
        setReady(true); // Show something instead of infinite spinner
        pushToast("⚠️ Map loaded partially (check console)", "info", 5000);
      }
    };

    initMap();

    // Cleanup
    return () => {
      if (safetyTimer) clearTimeout(safetyTimer);
      if (mapRef.current) {
        try {
          mapRef.current.remove();
        } catch (e) {
          console.warn("Cleanup error:", e);
        }
      }
      mapRef.current = null;
      tileRef.current = null;
      drawnRef.current = null;
      dcRef.current = null;
    };
  }, [analysisType]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── Tile switcher ───────────────────────────────────────── */
  const switchTile = useCallback((id) => {
    if (!mapRef.current || !tileRef.current) return;
    const t = TILES.find((x) => x.id === id);
    if (!t) return;
    mapRef.current.removeLayer(tileRef.current);
    tileRef.current = L.tileLayer(t.url, {
      attribution: t.attr,
      maxZoom: t.maxZoom,
      crossOrigin: "anonymous",
    }).addTo(mapRef.current);
    tileRef.current.bringToBack();
    setActiveTile(id);
  }, []);

  const fitBD = useCallback(
    () =>
      mapRef.current?.fitBounds(BD_BOUNDS, {
        padding: [48, 48],
        animate: true,
      }),
    [],
  );
  const fitSel = useCallback(() => {
    if (mapRef.current && sel)
      mapRef.current.fitBounds(sel.bounds, {
        padding: [60, 60],
        animate: true,
      });
  }, [sel]);
  const clearSel = useCallback(() => {
    drawnRef.current?.clearLayers();
    setSel(null);
    setResult(null);
  }, []);

  const activateDraw = useCallback(() => {
    if (!mapRef.current) {
      console.warn("❌ Map not ready");
      pushToast("⚠️ Map loading...", "error");
      return;
    }
    
    try {
      console.log("🖊️ Activating polygon drawing (4 points)");
      const map = mapRef.current;
      const cfg = CFG[analysisType] || CFG.deforestation;
      
      // Create polygon drawer for 4-point selection
      console.log("   Creating L.Draw.Polygon...");
      const poly = new L.Draw.Polygon(map, {
        shapeOptions: {
          color: cfg.color,
          weight: 2.5,
          opacity: 0.9,
          fillColor: cfg.color,
          fillOpacity: 0.15,
        },
        showArea: true,
        metric: true,
      });
      
      rectDrawerRef.current = poly;
      console.log("✅ Polygon instance created and stored");
      poly.enable();
      console.log("✅ Polygon drawing enabled!");
      pushToast("🖊️ Click 4 corners, then right-click to finish", "info");
      
    } catch (err) {
      console.error("❌ Error:", err.message);
      console.error("   L.Draw available:", !!L.Draw);
      console.error("   L.Draw.Polygon available:", !!L.Draw?.Polygon);
      pushToast("❌ " + err.message, "error");
    }
  }, [analysisType, pushToast]);

  /* ── Export PNG ──────────────────────────────────────────── */
  const exportPNG = useCallback(async () => {
    if (!mapRef.current || !sel) return;
    setExporting(true);
    setResult(null);

    try {
      const map = mapRef.current;
      const bounds = sel.bounds;
      
      console.log("📸 Starting export...");
      console.log("   Bounds:", bounds);
      
      // Fit map to selected bounds
      map.fitBounds(bounds, { padding: [15, 15], maxZoom: 16 });
      console.log("   Map fitted to bounds");
      
      // Wait for tiles to load
      await new Promise((r) => setTimeout(r, 1500));
      console.log("   Waited for tiles");

      const { default: h2c } = await import("html2canvas");
      console.log("   html2canvas imported");
      
      // Try capturing the whole map element first
      console.log("   Map element ref:", !!mapElRef.current);
      console.log("   Map element classes:", mapElRef.current?.className);
      
      const canvas = await h2c(mapElRef.current, {
        useCORS: true,
        allowTaint: true,
        scale: 1.5,
        backgroundColor: "#0d1b2a",
        logging: false,
        imageTimeout: 30000,
        onclone: (doc) => {
          console.log("   Cloning document...");
          const elementsToHide = [
            ".leaflet-control-zoom",
            ".leaflet-draw-toolbar",
            ".leaflet-control-attribution",
          ];
          elementsToHide.forEach((selector) => {
            doc.querySelectorAll(selector).forEach((el) => {
              el.style.display = "none";
              el.style.visibility = "hidden";
            });
          });
        },
      });

      console.log("✅ Canvas created:", canvas.width, "x", canvas.height);

      const dataUrl = canvas.toDataURL("image/png", 1.0);
      console.log("   Data URL length:", dataUrl.length);
      
      const bytes = Math.round((dataUrl.split(",")[1].length * 3) / 4);

      setResult({
        dataUrl,
        bytes,
        width: canvas.width,
        height: canvas.height,
        zoom: map.getZoom(),
        tileId: activeTile,
        bounds: bounds,
        ts: new Date().toISOString(),
      });

      console.log("✅ Result set:", { bytes, width: canvas.width, height: canvas.height });
      pushToast("🎉 Export ready! Preview shown below.", "success");
    } catch (err) {
      console.error("❌ Export error:", err.message);
      console.error("   Full error:", err);
      console.error("   Stack:", err.stack);
      pushToast("❌ Export failed: " + err.message, "error");
    } finally {
      setExporting(false);
    }
  }, [sel, activeTile, pushToast]);

  /* ── Download PNG ────────────────────────────────────────── */
  const downloadPNG = useCallback(() => {
    if (!result) return;
    const b = result.bounds;
    const date = result.ts.slice(0, 10);
    const name = `bd_${cfg.label.toLowerCase().replace(/\s+/g, "-")}_${b.getSouth().toFixed(3)}N_${b.getWest().toFixed(3)}E_z${result.zoom}_${date}.png`;
    const a = document.createElement("a");
    a.href = result.dataUrl;
    a.download = name;
    a.click();
    pushToast(`💾 Saved: "${name}"`, "success", 4500);
  }, [result, cfg.label, pushToast]);

  /* ── Derived bounds ──────────────────────────────────────── */
  const bi = sel
    ? {
        N: sel.bounds.getNorth(),
        S: sel.bounds.getSouth(),
        E: sel.bounds.getEast(),
        W: sel.bounds.getWest(),
        dLat: (sel.bounds.getNorth() - sel.bounds.getSouth()).toFixed(4),
        dLng: (sel.bounds.getEast() - sel.bounds.getWest()).toFixed(4),
      }
    : null;

  /* ── Render ──────────────────────────────────────────────── */
  return (
    <div className="mv-root">
      {/* ═══════════════════ Navbar ═══════════════════════════ */}
      <nav className="mv-nav glass animate-fade-in-down">
        <div className="mv-nav-l">
          <button className="btn btn-ghost btn-sm" onClick={onBack}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M10 3L5 8L10 13"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Back
          </button>

          <div className="mv-vdiv" />

          <div className="mv-pill" style={{ "--pc": cfg.color }}>
            <span className="mv-pill-icon">{cfg.icon}</span>
            <div>
              <div className="mv-pill-name">{cfg.label}</div>
              <div className="mv-pill-sub hide-mobile">{cfg.desc}</div>
            </div>
          </div>
        </div>

        <div className="mv-nav-r">
          <div className="mv-tiles hide-mobile">
            {TILES.map((t) => (
              <button
                key={t.id}
                className={`mv-tb ${activeTile === t.id ? "mv-tb--on" : ""}`}
                style={activeTile === t.id ? { "--tc": cfg.color } : {}}
                onClick={() => switchTile(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="mv-vdiv hide-mobile" />

          <button
            className="btn btn-ghost btn-sm"
            onClick={fitBD}
            data-tooltip="Fit Bangladesh"
          >
            <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
              <rect
                x="1.5"
                y="1.5"
                width="12"
                height="12"
                rx="2"
                stroke="currentColor"
                strokeWidth="1.5"
              />
              <path
                d="M5 7.5H10M7.5 5V10"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
            <span className="hide-mobile">BD</span>
          </button>

          <button
            className="btn btn-ghost btn-sm btn-icon"
            onClick={() => setSidebar((v) => !v)}
            data-tooltip={sidebar ? "Hide panel" : "Show panel"}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d={sidebar ? "M10 3L6 8L10 13" : "M6 3L10 8L6 13"}
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>
      </nav>

      {/* ═══════════════════ Body ═════════════════════════════ */}
      <div className="mv-body">
        {/* ── Map ──────────────────────────────────────────── */}
        <div className="mv-map-wrap">
          <div ref={mapElRef} className="mv-map" />

          {/* Loading spinner */}
          {!ready && (
            <div className="mv-loader">
              <div className="mv-loader-box animate-scale-in">
                <svg
                  className="animate-spin mv-loader-ring"
                  width="52"
                  height="52"
                  viewBox="0 0 52 52"
                  fill="none"
                >
                  <circle
                    cx="26"
                    cy="26"
                    r="22"
                    stroke="rgba(255,255,255,.1)"
                    strokeWidth="3"
                  />
                  <path
                    d="M26 4A22 22 0 0 1 48 26"
                    stroke={cfg.color}
                    strokeWidth="3"
                    strokeLinecap="round"
                  />
                </svg>
                <p className="mv-loader-txt">Loading Bangladesh map…</p>
                <span className={`badge ${cfg.badge}`}>
                  {cfg.icon} {cfg.label}
                </span>
              </div>
            </div>
          )}

          {/* Draw hint banner */}
          {drawing && (
            <div
              className="mv-draw-hint animate-fade-in-down"
              style={{ "--ac": cfg.color }}
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <rect
                  x="1"
                  y="1"
                  width="12"
                  height="12"
                  rx="2"
                  stroke={cfg.color}
                  strokeWidth="1.4"
                  strokeDasharray="3 2"
                />
              </svg>
              Click and drag on the map to draw a rectangle &nbsp;
              <kbd>Esc</kbd> to cancel
            </div>
          )}

          {/* Status bar */}
          <div className="mv-sb glass">
            <div className="mv-sb-l">
              <span
                className="mv-sb-dot"
                style={{
                  background: cfg.color,
                  boxShadow: `0 0 7px ${cfg.color}`,
                }}
              />
              <span className="mv-sb-txt">{cfg.label}</span>
              <div className="mv-sb-vd" />
              <span className="mv-sb-txt">z{zoom}</span>
              {mousePos && (
                <>
                  <div className="mv-sb-vd hide-mobile" />
                  <span className="mv-sb-coords hide-mobile">
                    {fmtCoord(mousePos.lat, "lat")} &nbsp;{" "}
                    {fmtCoord(mousePos.lng, "lng")}
                  </span>
                </>
              )}
            </div>
            <div className="mv-sb-r">
              {sel ? (
                <span
                  className="badge badge-green"
                  style={{ fontSize: "0.68rem" }}
                >
                  ✓ Region selected
                </span>
              ) : (
                <span className="mv-sb-hint">
                  Draw a rectangle to select a region
                </span>
              )}
            </div>
          </div>
        </div>

        {/* ── Sidebar ──────────────────────────────────────── */}
        <aside
          className={`mv-sidebar glass ${sidebar ? "mv-sidebar--open" : "mv-sidebar--closed"}`}
        >
          {/* §1 — Select Region */}
          <div className="mv-sec">
            <h3 className="mv-sec-h">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <rect
                  x="1"
                  y="1"
                  width="12"
                  height="12"
                  rx="2"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeDasharray="3 2"
                />
                <path
                  d="M4 7H10M7 4V10"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                />
              </svg>
              Select Region
            </h3>
            <p className="mv-sec-p">
              Use the <strong style={{ color: cfg.color }}>polygon tool</strong> to
              click <strong>4 points</strong> on the map, then right-click to finish.
            </p>
            <button
              className={`btn btn-sm w-full mv-draw-btn ${drawing ? "mv-draw-btn--on" : ""}`}
              style={{ "--bc": cfg.color, "--bg": cfg.glow }}
              onClick={activateDraw}
            >
              {drawing ? (
                <>
                  <span className="mv-pdot" style={{ background: cfg.color }} />
                  Drawing… (4 points + right-click)
                </>
              ) : (
                <>
                  <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                    <rect
                      x="1"
                      y="1"
                      width="11"
                      height="11"
                      rx="1.5"
                      stroke="currentColor"
                      strokeWidth="1.4"
                      strokeDasharray="3 2"
                    />
                  </svg>
                  Draw Area (4 Points)
                </>
              )}
            </button>
          </div>

          {/* §2 — Bounds Info */}
          {bi && (
            <div className="mv-sec animate-fade-in-up">
              <h3 className="mv-sec-h">
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <circle
                    cx="7"
                    cy="7"
                    r="6"
                    stroke="currentColor"
                    strokeWidth="1.4"
                  />
                  <path
                    d="M7 3.5V7L9 8.5"
                    stroke="currentColor"
                    strokeWidth="1.4"
                    strokeLinecap="round"
                  />
                </svg>
                Selection Bounds
              </h3>

              <div className="mv-bounds-grid">
                {[
                  { k: "North", v: fmtCoord(bi.N, "lat") },
                  { k: "South", v: fmtCoord(bi.S, "lat") },
                  { k: "East", v: fmtCoord(bi.E, "lng") },
                  { k: "West", v: fmtCoord(bi.W, "lng") },
                ].map(({ k, v }) => (
                  <div key={k} className="mv-bound">
                    <span className="mv-bk">{k}</span>
                    <span className="mv-bv">{v}</span>
                  </div>
                ))}
              </div>

              <div className="mv-spans">
                <div className="mv-span">
                  <span className="mv-sk">Δ Lat</span>
                  <span className="mv-sv">{bi.dLat}°</span>
                </div>
                <div className="mv-span">
                  <span className="mv-sk">Δ Lng</span>
                  <span className="mv-sv">{bi.dLng}°</span>
                </div>
              </div>

              <div className="mv-sel-btns">
                <button
                  className="btn btn-ghost btn-sm flex-1"
                  onClick={fitSel}
                >
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                    <path
                      d="M1 3.5V1H3.5M8.5 1H11V3.5M11 8.5V11H8.5M3.5 11H1V8.5"
                      stroke="currentColor"
                      strokeWidth="1.4"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  Zoom to
                </button>
                <button className="btn btn-danger btn-sm" onClick={clearSel}>
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                    <path
                      d="M2 2L10 10M10 2L2 10"
                      stroke="currentColor"
                      strokeWidth="1.7"
                      strokeLinecap="round"
                    />
                  </svg>
                  Clear
                </button>
              </div>

              <div style={{ marginTop: "10px", display: "flex", flexDirection: "column", gap: "8px" }}>
                {/* Date Selection */}
                <div style={{ background: "rgba(255,255,255,.05)", borderRadius: "8px", padding: "10px", border: "1px solid rgba(255,255,255,.1)" }}>
                  <div style={{ marginBottom: "10px" }}>
                    <label style={{ display: "block", fontSize: "12px", fontWeight: "600", marginBottom: "6px", color: "#9ca3af" }}>Old Date (Year/Month)</label>
                    <div style={{ display: "flex", gap: "8px" }}>
                      <input
                        type="number"
                        min="2000"
                        max={new Date().getFullYear()}
                        value={oldYear}
                        onChange={(e) => setOldYear(parseInt(e.target.value) || 2020)}
                        placeholder="Year"
                        style={{ flex: 1, padding: "6px", borderRadius: "4px", background: "rgba(0,0,0,.2)", border: "1px solid rgba(255,255,255,.1)", color: "white", fontSize: "13px" }}
                      />
                      <input
                        type="number"
                        min="1"
                        max="12"
                        value={oldMonth}
                        onChange={(e) => setOldMonth(Math.max(1, Math.min(12, parseInt(e.target.value) || 1)))}
                        placeholder="Month"
                        style={{ width: "60px", padding: "6px", borderRadius: "4px", background: "rgba(0,0,0,.2)", border: "1px solid rgba(255,255,255,.1)", color: "white", fontSize: "13px" }}
                      />
                    </div>
                  </div>

                  <div>
                    <label style={{ display: "block", fontSize: "12px", fontWeight: "600", marginBottom: "6px", color: "#9ca3af" }}>New Date (Year/Month)</label>
                    <div style={{ display: "flex", gap: "8px" }}>
                      <input
                        type="number"
                        min="2000"
                        max={new Date().getFullYear()}
                        value={newYear}
                        onChange={(e) => setNewYear(parseInt(e.target.value) || 2024)}
                        placeholder="Year"
                        style={{ flex: 1, padding: "6px", borderRadius: "4px", background: "rgba(0,0,0,.2)", border: "1px solid rgba(255,255,255,.1)", color: "white", fontSize: "13px" }}
                      />
                      <input
                        type="number"
                        min="1"
                        max="12"
                        value={newMonth}
                        onChange={(e) => setNewMonth(Math.max(1, Math.min(12, parseInt(e.target.value) || 1)))}
                        placeholder="Month"
                        style={{ width: "60px", padding: "6px", borderRadius: "4px", background: "rgba(0,0,0,.2)", border: "1px solid rgba(255,255,255,.1)", color: "white", fontSize: "13px" }}
                      />
                    </div>
                  </div>
                </div>

                <button
                  className="btn btn-primary btn-sm w-full"
                  onClick={() => sendToBackend()}
                  disabled={analyzing}
                >
                  {analyzing ? (
                    <>
                      <span className="spinner" />
                      Analyzing...
                    </>
                  ) : (
                    <>
                      <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                        <path
                          d="M7 1V9M4 6.5L7 9L10 6.5"
                          stroke="currentColor"
                          strokeWidth="1.5"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                        <path
                          d="M1 11H13"
                          stroke="currentColor"
                          strokeWidth="1.5"
                          strokeLinecap="round"
                        />
                      </svg>
                      Analyze Change
                    </>
                  )}
                </button>

                {analysisResult?.stats && (
                  <div className="mv-sec" style={{ margin: 0 }}>
                    <div style={{ fontSize: "11px", color: "#9ca3af", marginBottom: "6px" }}>Analysis Results:</div>
                    <div className="mv-meta-row" style={{ marginBottom: "4px" }}>
                      <span className="mv-meta-k" style={{ fontSize: "11px" }}>Old</span>
                      <span className="mv-meta-v" style={{ fontSize: "11px" }}>{analysisResult.stats.oldDate}</span>
                    </div>
                    <div className="mv-meta-row" style={{ marginBottom: "4px" }}>
                      <span className="mv-meta-k" style={{ fontSize: "11px" }}>New</span>
                      <span className="mv-meta-v" style={{ fontSize: "11px" }}>{analysisResult.stats.newDate}</span>
                    </div>
                    <div className="mv-meta-row" style={{ marginBottom: "4px" }}>
                      <span className="mv-meta-k" style={{ fontSize: "11px", color: "#ef4444" }}>Deforestation</span>
                      <span className="mv-meta-v" style={{ fontSize: "11px", color: "#ef4444" }}>{analysisResult.stats.deforestation} px</span>
                    </div>
                    <div className="mv-meta-row" style={{ marginBottom: "4px" }}>
                      <span className="mv-meta-k" style={{ fontSize: "11px", color: "#f97316" }}>Water Loss</span>
                      <span className="mv-meta-v" style={{ fontSize: "11px", color: "#f97316" }}>{analysisResult.stats.waterLoss} px</span>
                    </div>
                    <div className="mv-meta-row">
                      <span className="mv-meta-k" style={{ fontSize: "11px" }}>Total Pixels</span>
                      <span className="mv-meta-v" style={{ fontSize: "11px" }}>{analysisResult.stats.totalPixels}</span>
                    </div>
                    <div style={{ marginTop: "8px", display: "flex", gap: "10px", fontSize: "10px" }}>
                      <span style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                        <span style={{ width: "10px", height: "10px", borderRadius: "50%", background: "#ef4444", display: "inline-block" }} />
                        Deforestation
                      </span>
                      <span style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                        <span style={{ width: "10px", height: "10px", borderRadius: "50%", background: "#f97316", display: "inline-block" }} />
                        Water Loss
                      </span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* §3 — Export */}
          {sel && (
            <div className="mv-sec animate-fade-in-up">
              <h3 className="mv-sec-h">
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path
                    d="M7 1V9M4 6.5L7 9L10 6.5"
                    stroke="currentColor"
                    strokeWidth="1.4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <path
                    d="M2 11.5H12"
                    stroke="currentColor"
                    strokeWidth="1.4"
                    strokeLinecap="round"
                  />
                </svg>
                Export as PNG
              </h3>
              <p className="mv-sec-p">
                Captures the map at <strong>2× resolution</strong> — includes
                tiles, Bangladesh border, and your selection overlay.
              </p>

              <button
                className={`btn btn-sm w-full mv-exp-btn ${exporting ? "mv-exp-btn--busy" : ""}`}
                style={{ "--bc": cfg.color, "--bg": cfg.glow }}
                onClick={exportPNG}
                disabled={exporting}
              >
                {exporting ? (
                  <>
                    <span className="spinner" />
                    Rendering map…
                  </>
                ) : (
                  <>
                    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                      <rect
                        x="1"
                        y="1"
                        width="12"
                        height="12"
                        rx="2"
                        stroke="currentColor"
                        strokeWidth="1.4"
                      />
                      <path
                        d="M7 4V9M4.5 6.5L7 9L9.5 6.5"
                        stroke="currentColor"
                        strokeWidth="1.4"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    Export as PNG
                  </>
                )}
              </button>

              {/* Result preview */}
              {result && (
                <div className="mv-result animate-scale-in">
                  <div className="mv-result-img-wrap">
                    <img
                      src={result.dataUrl}
                      alt="Exported region"
                      className="mv-result-img"
                    />
                    <div className="mv-result-badge">
                      <span className={`badge ${cfg.badge}`}>{cfg.label}</span>
                    </div>
                  </div>

                  <div className="mv-result-meta">
                    {[
                      ["File size", fmtBytes(result.bytes)],
                      ["Resolution", `${result.width} × ${result.height}px`],
                      ["Zoom level", `z${result.zoom}`],
                      [
                        "Base layer",
                        TILES.find((t) => t.id === result.tileId)?.label || "—",
                      ],
                      ["Exported", result.ts.slice(11, 19) + " UTC"],
                    ].map(([k, v]) => (
                      <div key={k} className="mv-meta-row">
                        <span className="mv-meta-k">{k}</span>
                        <span className="mv-meta-v">{v}</span>
                      </div>
                    ))}
                  </div>

                  <div style={{ display: "flex", gap: "8px", flexDirection: "column" }}>
                    <button
                      className="btn btn-primary btn-sm w-full"
                      onClick={sendToBackend}
                      disabled={analyzing}
                    >
                      {analyzing ? (
                        <>
                          <span className="spinner" />
                          Analyzing...
                        </>
                      ) : (
                        <>
                          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                            <path
                              d="M7 1V9M4 6.5L7 9L10 6.5"
                              stroke="currentColor"
                              strokeWidth="1.5"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                            <circle cx="2" cy="12" r="1" fill="currentColor" />
                            <circle cx="7" cy="12" r="1" fill="currentColor" />
                            <circle cx="12" cy="12" r="1" fill="currentColor" />
                          </svg>
                          Send to Backend
                        </>
                      )}
                    </button>

                    <button
                      className="btn btn-success btn-sm w-full"
                      onClick={downloadPNG}
                    >
                      <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                        <path
                          d="M7 1V9M4 6.5L7 9L10 6.5"
                          stroke="currentColor"
                          strokeWidth="1.5"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                        <path
                          d="M1 11H13"
                          stroke="currentColor"
                          strokeWidth="1.5"
                          strokeLinecap="round"
                        />
                      </svg>
                      Download PNG
                    </button>
                  </div>

                  {/* Analysis Results */}
                  {analysisResult && (
                    <div
                      className="mv-sec animate-fade-in-up"
                      style={{
                        marginTop: "16px",
                        padding: "12px",
                        background: `color-mix(in srgb, ${cfg.color} 12%, transparent)`,
                        border: `1px solid color-mix(in srgb, ${cfg.color} 30%, transparent)`,
                        borderRadius: "8px",
                      }}
                    >
                      <h4 style={{ margin: "0 0 10px 0", color: cfg.color, fontSize: "0.9rem" }}>
                        📊 Analysis Results
                      </h4>
                      <div style={{ fontSize: "0.85rem", lineHeight: "1.6", color: "var(--text-secondary)" }}>
                        {typeof analysisResult.data === "object" ? (
                          Object.entries(analysisResult.data).map(([key, value]) => (
                            <div key={key} style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px" }}>
                              <span style={{ fontWeight: "500", textTransform: "capitalize" }}>
                                {key.replace(/_/g, " ")}:
                              </span>
                              <span style={{ color: "#f1f5f9" }}>
                                {typeof value === "number"
                                  ? value.toFixed(2)
                                  : typeof value === "boolean"
                                    ? value
                                      ? "Yes"
                                      : "No"
                                    : String(value)}
                              </span>
                            </div>
                          ))
                        ) : (
                          <p>{String(analysisResult.data)}</p>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </aside>
      </div>

      {/* ═══════════════════ Toast ════════════════════════════ */}
      {toast && (
        <div
          key={toast.key}
          className={`mv-toast mv-toast--${toast.type} animate-fade-in-up glass`}
        >
          {toast.msg}
        </div>
      )}
    </div>
  );
}
