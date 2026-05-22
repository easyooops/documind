/**
 * Stop leftover DocuMind dev servers on ports 8000 and 3000.
 */
import { tryFreePort } from "./lib.mjs";

console.log("[documind] Cleaning dev ports...\n");
await tryFreePort("API", 8000);
await tryFreePort("Web", 3000);
console.log("\nDone. Run npm run dev to start.\n");
