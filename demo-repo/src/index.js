import React from "react";
import ReactDOM from "react-dom/client";
import Header from "./components/header";

const root = ReactDOM.createRoot(document.getElementById("root"));

root.render(
  <React.StrictMode>
    <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100vh", width: "100vw", overflow: "hidden" }}>
      <Header />
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100%" }}>
        🦆
      </div>
    </div>
  </React.StrictMode>,
);

