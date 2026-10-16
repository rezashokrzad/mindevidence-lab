// Frontend configuration. No secrets ever go in this file.
// The API base URL can be overridden before the app loads, for example:
//   <script>window.MINDEVIDENCE_API_BASE = 'http://localhost:8001';</script>
export const API_BASE_URL = (window.MINDEVIDENCE_API_BASE ?? 'http://localhost:8000').replace(/\/+$/, '');

// Abort API requests that take longer than this (milliseconds).
export const API_TIMEOUT_MS = 10000;
