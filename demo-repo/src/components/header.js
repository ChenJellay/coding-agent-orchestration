import React from "react";

export default function Header() {
  return (
    <header style={{ padding: "1rem", backgroundColor: "#282c34" }}>
      <h1 style={{ color: "yellow", margin: 0 }}>Demo App</h1>
      <button
        style={{
          marginTop: "0.5rem",
          backgroundColor: "pink",
          color: "white",
          border: "none",
          borderRadius: "4px",
          cursor: "pointer",
        }}
      >
        Click me
      </button>
    </header>
  );
}
