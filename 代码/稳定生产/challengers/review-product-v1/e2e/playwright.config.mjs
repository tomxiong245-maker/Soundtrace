// Playwright config for P1 review-product-v1 E2E
export default {
  testDir: "./e2e",
  timeout: 60_000,
  reporter: [["list"], ["json", { outputFile: "e2e-report.json" }]],
  use: {
    headless: true,
    viewport: { width: 1280, height: 900 },
    // 禁用不必要的网络：Chromium 只访问 localhost
    launchOptions: {
      args: [
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-background-networking",
        "--disable-features=OptimizationHints,MediaRouter",
      ],
    },
  },
};
