# Campus Customs Back Office (dashboard)

React + Vite + TypeScript board for the Campus Customs agent team. It calls the FastAPI backend at `http://localhost:8000` (see `src/api.ts`; override it with `VITE_API_URL`).

```bash
cd backend && ../.venv/bin/python -m uvicorn main:app --reload --port 8000
```
```bash
cd frontend && npm install && npm run dev
```

Then open http://localhost:5173. The backend's CORS settings allow this origin. The design is explained in `../output/design.md`.
