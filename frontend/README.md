# Patel Propfirm Blaster dashboard

Next.js operations dashboard for Patel Propfirm Blaster. Configuration is read from `.env.local`; only public API routing values belong in the frontend environment.

```powershell
npm install
npm run dev
```

Production uses `npm run build` followed by `npm start`. Place a TLS reverse proxy in front of the self-hosted Node service.
