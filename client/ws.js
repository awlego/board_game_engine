// Single source of truth for the game server address.
// Override at build time with VITE_WS_URL when hosting behind a reverse
// proxy: either a full URL (wss://example.com/ws/engine) or a path
// (/ws/engine), which connects same-origin with ws/wss chosen to match the
// page. The default keeps the LAN-friendly behavior of connecting straight
// to the server on port 8765.
const configured = import.meta.env.VITE_WS_URL;
export const WS_URL = !configured
  ? `ws://${window.location.hostname}:8765`
  : configured.startsWith("/")
    ? `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}${configured}`
    : configured;
