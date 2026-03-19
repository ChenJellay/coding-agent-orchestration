## Frontend (Monitorer’s Cockpit)

Vite + React/TypeScript UI for the Agenti-Helix control plane.

### Run locally

```bash
npm install
npm run dev
```

### API configuration

By default the UI calls `http://127.0.0.1:8001`.

To override:

```bash
export VITE_API_BASE_URL="http://127.0.0.1:8001"
```

