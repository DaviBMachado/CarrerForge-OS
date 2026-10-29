import { useEffect, useState } from "react";

interface HealthStatus {
  status: string;
  app: string;
  version: string;
}

export function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/health")
      .then((res) => {
        if (!res.ok) {
          throw new Error(`HTTP error! status: ${res.status}`);
        }
        return res.json();
      })
      .then((data: HealthStatus) => {
        setHealth(data);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  return (
    <div
      style={{
        maxWidth: "600px",
        margin: "0 auto",
        padding: "2rem",
        backgroundColor: "#1e293b",
        borderRadius: "12px",
        color: "#f8fafc",
        boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.3)",
      }}
    >
      <header style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ margin: "0 0 0.5rem 0", fontSize: "1.75rem", color: "#38bdf8" }}>
          CareerForge OS
        </h1>
        <p style={{ margin: 0, color: "#94a3b8", fontSize: "0.95rem" }}>
          Monolith Architecture & Unified Runtime
        </p>
      </header>

      <section
        style={{
          padding: "1rem",
          backgroundColor: "#0f172a",
          borderRadius: "8px",
          border: "1px solid #334155",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.75rem" }}>
          <div
            style={{
              width: "10px",
              height: "10px",
              borderRadius: "50%",
              backgroundColor: health?.status === "healthy" ? "#22c55e" : error ? "#ef4444" : "#eab308",
            }}
          />
          <strong style={{ fontSize: "1rem" }}>
            {loading ? "Checking System Status..." : health ? "System Online" : "System Error"}
          </strong>
        </div>

        {health && (
          <div style={{ fontSize: "0.9rem", color: "#cbd5e1", lineHeight: "1.6" }}>
            <div>
              <strong>Service:</strong> {health.app}
            </div>
            <div>
              <strong>Status:</strong> {health.status}
            </div>
            <div>
              <strong>Version:</strong> {health.version}
            </div>
            <div>
              <strong>Environment:</strong> Unified Monolith (FastAPI + Vite/React)
            </div>
          </div>
        )}

        {error && (
          <p style={{ color: "#f87171", fontSize: "0.9rem", margin: 0 }}>
            Failed to connect to backend: {error}
          </p>
        )}
      </section>
    </div>
  );
}

export default App;
