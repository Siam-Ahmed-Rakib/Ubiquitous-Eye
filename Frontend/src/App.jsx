import { useState } from "react";
import LayerSelection from "./components/LayerSelection";
import MapView from "./components/MapView";
import "./App.css";

export default function App() {
  const [page, setPage] = useState("select"); // 'select' | 'map'
  const [analysisType, setAnalysisType] = useState(null);

  const handleSelect = (type) => {
    setAnalysisType(type);
    setPage("map");
  };

  const handleBack = () => {
    setPage("select");
    setAnalysisType(null);
  };

  return (
    <div className="app-root">
      {page === "select" && <LayerSelection onSelect={handleSelect} />}
      {page === "map" && analysisType && (
        <MapView analysisType={analysisType} onBack={handleBack} />
      )}
    </div>
  );
}
