// The only per-environment setting. Empty string means "same origin as this page", which is every way
// this runs today: local uvicorn, ngrok, and the Lambda, which serves these files and the API itself.
// Set it to an API origin only if the frontend is ever hosted somewhere separate.
window.ENCORE_CONFIG = { apiOrigin: "" };
