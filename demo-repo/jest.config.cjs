/** @type {import("jest").Config} */
module.exports = {
  testEnvironment: "jsdom",
  testMatch: ["**/*.test.js", "**/*.test.jsx"],
  moduleFileExtensions: ["js", "jsx"],
  transform: {
    "^.+\\.(js|jsx)$": "babel-jest",
  },
  setupFilesAfterEnv: ["<rootDir>/jest.setup.js"],
  clearMocks: true,
};
