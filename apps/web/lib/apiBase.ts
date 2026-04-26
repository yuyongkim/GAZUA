// Runtime API base resolver — picks the right backend for the current origin.
//
// The site is reachable at multiple hostnames (gazua.yule.pics, alpha.yule.pics,
// localhost). Each has its own paired API host (gazua-api.yule.pics, alpha-api…,
// localhost:31300). We can't hard-bake a single NEXT_PUBLIC_API_BASE_URL because
// that breaks the other origins — Chrome blocks loopback fetches from public
// origins (Private Network Access), and public fetches from localhost waste
// a hop through the tunnel.
//
// Rule:
//   - If running server-side (SSR) → use env var, fall back to loopback.
//   - If running client-side (browser):
//       - 127.0.0.1 / localhost → env var, else http://{host}:31300
//       - anything else (gazua.*, alpha.*, future subdomains) → derive
//         `<subdomain>-api.<rest-of-host>` with matching protocol.

const DEV_API_PORT = 31300;
const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "0.0.0.0", "::1"]);

export function apiBase(): string {
  const envValue = process.env.NEXT_PUBLIC_API_BASE_URL;

  if (typeof window === "undefined") {
    // Server-side / build-time. Use env var verbatim if set; otherwise dev loopback.
    return envValue ?? `http://127.0.0.1:${DEV_API_PORT}`;
  }

  const { protocol, hostname } = window.location;

  if (LOCAL_HOSTS.has(hostname)) {
    return envValue ?? `${protocol}//${hostname}:${DEV_API_PORT}`;
  }

  // Public: derive `<first-label>-api.<rest>` from the hostname.
  // e.g. gazua.yule.pics → gazua-api.yule.pics
  //      alpha.yule.pics → alpha-api.yule.pics
  const firstDot = hostname.indexOf(".");
  if (firstDot === -1) {
    // No dot — can't derive. Fall back to env var or same origin.
    return envValue ?? `${protocol}//${hostname}`;
  }
  const sub = hostname.slice(0, firstDot);
  const rest = hostname.slice(firstDot + 1);
  return `${protocol}//${sub}-api.${rest}`;
}
