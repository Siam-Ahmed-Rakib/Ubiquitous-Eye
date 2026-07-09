import { useState } from "react";
import "./LayerSelection.css";

const LAYERS = [
  {
    id: "deforestation",
    title: "Deforestation",
    subtitle: "Forest Cover Loss Analysis",
    description:
      "Detect and monitor forest cover loss, illegal logging, and vegetation degradation across Bangladesh using multi-temporal satellite imagery.",
    icon: (
      <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="32" cy="32" r="30" fill="rgba(34,197,94,0.12)" stroke="rgba(34,197,94,0.4)" strokeWidth="1.5" />
        <path d="M32 10 L20 28 H26 L18 42 H30 V54 H34 V42 H46 L38 28 H44 Z" fill="rgba(34,197,94,0.85)" />
        <line x1="14" y1="48" x2="50" y2="48" stroke="#ef4444" strokeWidth="2.5" strokeLinecap="round" />
        <line x1="12" y1="52" x2="28" y2="52" stroke="#ef4444" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="4 3" />
        <line x1="36" y1="52" x2="52" y2="52" stroke="#ef4444" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="4 3" />
      </svg>
    ),
    accentVar: "--defore-primary",
    accentColor: "#22c55e",
    glowColor: "rgba(34,197,94,0.25)",
    badgeClass: "badge-green",
    badgeLabel: "Vegetation",
    stats: [
      { label: "Layers", value: "12" },
      { label: "Resolution", value: "10m" },
      { label: "Update", value: "Monthly" },
    ],
    bgPattern: "deforestation",
  },
  {
    id: "land_encroachment",
    title: "Land Encroachment",
    subtitle: "Illegal Land Use Detection",
    description:
      "Identify unauthorized settlements, agricultural encroachments, and land-use changes in protected zones and river floodplains.",
    icon: (
      <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="32" cy="32" r="30" fill="rgba(245,158,11,0.12)" stroke="rgba(245,158,11,0.4)" strokeWidth="1.5" />
        <rect x="16" y="28" width="14" height="18" rx="2" fill="rgba(245,158,11,0.8)" />
        <rect x="34" y="22" width="14" height="24" rx="2" fill="rgba(245,158,11,0.5)" />
        <path d="M12 46 H52" stroke="rgba(245,158,11,0.6)" strokeWidth="1.5" />
        <path d="M20 28 L32 14 L44 22" stroke="#f59e0b" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="46" cy="20" r="6" fill="rgba(239,68,68,0.9)" />
        <line x1="43" y1="20" x2="49" y2="20" stroke="white" strokeWidth="2" strokeLinecap="round" />
        <line x1="46" y1="17" x2="46" y2="23" stroke="white" strokeWidth="2" strokeLinecap="round" />
      </svg>
    ),
    accentVar: "--encroach-primary",
    accentColor: "#f59e0b",
    glowColor: "rgba(245,158,11,0.25)",
    badgeClass: "badge-amber",
    badgeLabel: "Land Use",
    stats: [
      { label: "Layers", value: "8" },
      { label: "Resolution", value: "5m" },
      { label: "Update", value: "Weekly" },
    ],
    bgPattern: "encroachment",
  },
  {
    id: "river_erosion",
    title: "River Erosion",
    subtitle: "Riverbank Change Monitoring",
    description:
      "Track riverbank erosion, channel migration, and sediment dynamics along the Padma, Meghna, Jamuna and other major river systems.",
    icon: (
      <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="32" cy="32" r="30" fill="rgba(59,130,246,0.12)" stroke="rgba(59,130,246,0.4)" strokeWidth="1.5" />
        <path d="M10 38 Q16 30 22 36 Q28 42 34 34 Q40 26 46 32 Q52 38 54 34"
          stroke="#3b82f6" strokeWidth="3" strokeLinecap="round" fill="none" />
        <path d="M10 44 Q16 36 22 42 Q28 48 34 40 Q40 32 46 38 Q52 44 54 40"
          stroke="rgba(59,130,246,0.5)" strokeWidth="2" strokeLinecap="round" fill="none" />
        <path d="M10 38 Q16 30 22 36 Q28 42 34 34 Q40 26 46 32 Q52 38 54 34 L54 56 L10 56 Z"
          fill="rgba(59,130,246,0.15)" />
        <path d="M18 24 L18 14 M24 22 L24 10 M30 20 L30 8"
          stroke="#ef4444" strokeWidth="2" strokeLinecap="round" strokeDasharray="2 2" />
      </svg>
    ),
    accentVar: "--erosion-primary",
    accentColor: "#3b82f6",
    glowColor: "rgba(59,130,246,0.25)",
    badgeClass: "badge-blue",
    badgeLabel: "Hydrology",
    stats: [
      { label: "Layers", value: "15" },
      { label: "Resolution", value: "30m" },
      { label: "Update", value: "Bi-Weekly" },
    ],
    bgPattern: "erosion",
  },
];

export default function LayerSelection({ onSelect }) {
  const [hovered, setHovered] = useState(null);
  const [selected, setSelected] = useState(null);
  const [confirming, setConfirming] = useState(false);

  const handleSelect = (id) => {
    setSelected(id);
  };

  const handleConfirm = () => {
    if (!selected) return;
    setConfirming(true);
    setTimeout(() => {
      onSelect(selected);
    }, 500);
  };

  const selectedLayer = LAYERS.find((l) => l.id === selected);

  return (
    <div className="layer-selection-page">
      {/* Animated background */}
      <div className="ls-bg-grid" aria-hidden="true" />
      <div className="ls-bg-glow ls-bg-glow--tl" aria-hidden="true" />
      <div className="ls-bg-glow ls-bg-glow--br" aria-hidden="true" />

      <div className="ls-container">
        {/* Header */}
        <header className="ls-header animate-fade-in-down">
          <div className="ls-logo">
            <span className="ls-logo-icon">
              <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
                <path d="M14 2L3 7.5V14C3 19.8 8.1 25.2 14 26.5C19.9 25.2 25 19.8 25 14V7.5L14 2Z"
                  fill="rgba(59,130,246,0.2)" stroke="#3b82f6" strokeWidth="1.5" strokeLinejoin="round" />
                <path d="M9 14L12.5 17.5L19 11" stroke="#22c55e" strokeWidth="2"
                  strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <span className="ls-logo-text">BD GeoSense</span>
            <span className="badge badge-teal">v2.0</span>
          </div>
          <div className="ls-header-meta">
            <span className="badge badge-green">
              <span className="ls-dot ls-dot--green" />
              Live Data
            </span>
            <span className="badge badge-blue">Bangladesh</span>
          </div>
        </header>

        {/* Page title */}
        <div className="ls-title-block animate-fade-in-up delay-100">
          <h1 className="ls-title">
            Choose Analysis Layer
          </h1>
          <p className="ls-subtitle">
            Select an environmental monitoring layer to analyze satellite imagery
            over Bangladesh. Draw a region of interest on the interactive map to
            export a georeferenced PNG.
          </p>
        </div>

        {/* Cards grid */}
        <div className="ls-grid">
          {LAYERS.map((layer, idx) => (
            <button
              key={layer.id}
              className={[
                "ls-card",
                `ls-card--${layer.bgPattern}`,
                hovered === layer.id ? "ls-card--hovered" : "",
                selected === layer.id ? "ls-card--selected" : "",
                `animate-fade-in-up delay-${(idx + 2) * 100}`,
              ]
                .filter(Boolean)
                .join(" ")}
              style={{
                "--card-accent": layer.accentColor,
                "--card-glow": layer.glowColor,
              }}
              onMouseEnter={() => setHovered(layer.id)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => handleSelect(layer.id)}
              aria-pressed={selected === layer.id}
              aria-label={`Select ${layer.title} analysis layer`}
            >
              {/* Selection indicator */}
              <div className="ls-card-check">
                {selected === layer.id ? (
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <circle cx="8" cy="8" r="8" fill="var(--card-accent)" />
                    <path d="M4.5 8L7 10.5L11.5 5.5" stroke="white"
                      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : (
                  <div className="ls-card-check-empty" />
                )}
              </div>

              {/* Card header */}
              <div className="ls-card-top">
                <div className="ls-card-icon">{layer.icon}</div>
                <div className="ls-card-meta">
                  <span className={`badge ${layer.badgeClass}`}>
                    {layer.badgeLabel}
                  </span>
                </div>
              </div>

              {/* Card content */}
              <div className="ls-card-body">
                <h3 className="ls-card-title">{layer.title}</h3>
                <p className="ls-card-sub">{layer.subtitle}</p>
                <p className="ls-card-desc">{layer.description}</p>
              </div>

              {/* Stats row */}
              <div className="ls-card-stats">
                {layer.stats.map((s) => (
                  <div key={s.label} className="ls-stat">
                    <span className="ls-stat-value">{s.value}</span>
                    <span className="ls-stat-label">{s.label}</span>
                  </div>
                ))}
              </div>

              {/* Bottom glow bar */}
              <div className="ls-card-glow-bar" />
            </button>
          ))}
        </div>

        {/* Footer action bar */}
        <div
          className={[
            "ls-action-bar",
            selected ? "ls-action-bar--visible" : "",
            confirming ? "ls-action-bar--confirming" : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          <div className="ls-action-info">
            {selectedLayer && (
              <>
                <div
                  className="ls-action-dot"
                  style={{ background: selectedLayer.accentColor }}
                />
                <span className="ls-action-label">
                  <span className="text-secondary">Selected:</span>{" "}
                  <strong style={{ color: selectedLayer.accentColor }}>
                    {selectedLayer.title}
                  </strong>
                </span>
              </>
            )}
          </div>
          <div className="ls-action-btns">
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setSelected(null)}
              disabled={confirming}
            >
              Cancel
            </button>
            <button
              className={`btn btn-primary btn-lg ls-confirm-btn ${confirming ? "ls-confirm-btn--loading" : ""}`}
              onClick={handleConfirm}
              disabled={!selected || confirming}
            >
              {confirming ? (
                <>
                  <span className="spinner" />
                  Loading Map…
                </>
              ) : (
                <>
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M3 8H13M9 4L13 8L9 12" stroke="currentColor"
                      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  Open Map
                </>
              )}
            </button>
          </div>
        </div>

        {/* Footer note */}
        <footer className="ls-footer animate-fade-in delay-600">
          <p>
            Powered by{" "}
            <span style={{ color: "#38bdf8" }}>OpenStreetMap</span> ·{" "}
            <span style={{ color: "#818cf8" }}>Esri Satellite</span> ·{" "}
            <span style={{ color: "#a3e635" }}>Sentinel-2</span>
          </p>
          <p>Data updated regularly · All analysis is for research purposes</p>
        </footer>
      </div>
    </div>
  );
}
