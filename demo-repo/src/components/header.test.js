import React from "react";
import { render, screen } from "@testing-library/react";
import Header from "./header";

test("renders demo title", () => {
  render(<Header />);
  expect(screen.getByRole("heading", { name: /demo app/i })).toBeInTheDocument();
});
