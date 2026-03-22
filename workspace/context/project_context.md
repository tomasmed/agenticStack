# Project Context
*Stack conventions and deployment rules. Handed to TeamLead and Developer at runtime.*

## Stack
- **Frontend:** Next.js 14, App Router, Tailwind CSS, TypeScript
- **Database:** PostgreSQL via Neon — psycopg2, connection string from env
- **Agent API:** FastAPI in backend/ — kicks agentic flows, minimal routes
- **Hosting:** Vercel (frontend), Railway (backend), Neon (database)

## Folder conventions
```
frontend/
  app/
    layout.tsx               ← html/body shell, imports globals.css
    page.tsx                 ← root page, server component by default
    globals.css              ← Tailwind directives only, imported in layout.tsx
    components/
      [ComponentName]/
        [ComponentName].tsx  ← 'use client' if needed
    api/
      [route]/
        route.ts             ← Next.js API route
  public/
    images/                  ← referenced as /images/file.png
    icons/

backend/
  main.py                    ← FastAPI entry point
  requirements.txt           ← fastapi, uvicorn, psycopg2 only
  db/
    schema.py                ← ensure_schema() + stored procedures
```

## CSS rules
- Tailwind CSS — utility classes preferred over custom CSS
- Custom CSS only when Tailwind can't express it cleanly
- No inline style= except for truly dynamic values (calculated at runtime)
- globals.css contains Tailwind directives only — imported in layout.js exclusively

## TypeScript rules
- Strict mode on — no `any` unless absolutely necessary
- Props interfaces defined inline or in same file for components
- API route handlers typed with `NextRequest` / `NextResponse`
- Server Components are async by default — type accordingly

## Next.js rules
- `'use client'` required on any component using useState, useEffect, or event handlers
- Static assets: `url('/images/file.png')` — never `/public/` prefix
- API routes: `export async function GET(request) { return Response.json({}) }`
- App Router only — no `pages/` directory, no `getServerSideProps`
- Server Components by default — only opt into client where interactivity needed

## Database rules
- All DB calls server-side only — API routes or Server Components, never client components
- Schema managed via `ensure_schema()` on boot — no migration framework
- Business logic queries via stored procedures (`CREATE OR REPLACE`)
- Use connection pooling — psycopg2 `SimpleConnectionPool`
- Never expose connection string to client bundle

## Agent API (FastAPI — Railway)
- Lives in `agent_api/main.py`
- Two routes only for now: `POST /kickoff` and `GET /status`
- Called from Next.js API routes — never directly from client
- CORS restricted to Vercel domain

## Git conventions
- One commit per ticket: `T-[number]: [title]`
- Branch: `feature/{run-id}`
- Never commit `.env` or `db/` data files