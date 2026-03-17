import React from "react";

export default function Header() {
  return (
    <header style={{ padding: "1rem" }}>
      <button
        style={{
          backgroundColor: "blue",
          color: "white",
          padding: "0.5rem 1rem",
          border: "none",
          borderRadius: "4px",
        }}
      >
        Click me
      </button>
    </header>
  );
}

