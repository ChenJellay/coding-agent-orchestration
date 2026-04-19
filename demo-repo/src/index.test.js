import React from "react";
import ReactDOM from "react-dom/client";
import { render } from "@testing-library/react";
import "@testing-library/jest-dom";
import index from "./index.jsx";

// Mock external dependencies
jest.mock("react-dom/client", () => ({
  createRoot: jest.fn((element) => ({
    render: jest.fn((node) => {
      // Simulate React rendering by attaching node to the mock element
      element.children = [...element.children, node];
    }),
  })),
}));

jest.mock("./components/header", () => ({
  default: () => (
    <div data-testid="header">
      <span>Header Content</span>
    </div>
  ),
}));

describe("index.jsx", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("renders rubber ducky emoji centered on the page", () => {
    // Setup mock document
    const mockRootElement = {
      id: "root",
      children: [],
      style: {},
      classList: {
        contains: jest.fn(),
        add: jest.fn(),
        remove: jest.fn(),
      },
    };

    const mockDocument = {
      getElementById: jest.fn((id) => (id === "root" ? mockRootElement : null)),
    };

    // Override global document for this test
    const originalDocument = global.document;
    global.document = mockDocument;

    try {
      // Import index inside the test to pick up the mocks
      const { default: Index } = require("./index.jsx");

      // Trigger render
      root.render(
        <React.StrictMode>
          <div>
            <Index />
          </div>
        </React.StrictMode>
      );

      // Assert root element exists
      expect(mockDocument.getElementById("root")).toBeTruthy();

      // Assert Header is rendered
      expect(mockRootElement.children).toContainEqual(
        expect.objectContaining({ type: 'div' })
      );

      // Assert rubber ducky emoji is rendered
      const renderedContent = mockRootElement.children;
      const duckyIndex = renderedContent.findIndex(
        (child) => child.props.children === "\ud83e\udd86"
      );

      expect(duckyIndex).toBeGreaterThanOrEqual(0);

      // Assert centering (checking for common centering styles)
      const duckyElement = renderedContent[duckyIndex];
      
      // Check if inline styles are used for centering
      if (duckyElement.style) {
        expect(duckyElement.style.top).toBe("50%");
        expect(duckyElement.style.left).toBe("50%");
        expect(duckyElement.style.position).toBe("absolute");
      }

      // Check if CSS classes are used for centering
      if (!duckyElement.style.top) {
        expect(duckyElement.classList.contains("centered")).toBe(true);
      }
    } finally {
      global.document = originalDocument;
    }
  });
});
