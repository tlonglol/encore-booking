// The only per-environment setting. Empty string means "same origin as this page" (local uvicorn or ngrok).
// For a split deploy, set it to the API's origin, e.g. "https://xyz.lambda-url.us-east-1.on.aws".
window.ENCORE_CONFIG = { apiOrigin: "" };
