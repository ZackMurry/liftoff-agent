# Liftoff Portal

Next.js management portal for Liftoff, the drone simulation CI platform.

## Local Development

```bash
npm install
npm run dev
```

Open `http://localhost:3000`.

## Current Scope

The app starts as a static MVP dashboard backed by typed mock data in `lib/mock-data.ts`.
The first integration points are:

- GitHub App or webhook run creation
- Supabase `runs` and `experiments` tables
- Simulation runner `POST /run`
- Agent-generated PR review summaries
