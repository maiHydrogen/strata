# STRATA demo runbook

The hosted demo chain:

```
User's browser
  -> Vercel (Next.js web app)
  -> ngrok (fixed address) -> STRATA API on the workstation (WSL, port 8001)
       -> STAAD.Pro, headless (Windows)
       -> building JSON (C:\Users\Public\Documents\STRATA)
       -> Revit 2027 + pyRevit Routes (Windows, localhost:48884): new project from the STRATA template,
          build, stairs, IFC export, save .rvt
       -> IFC check (IfcOpenShell)
  <- report, IFC check, .rvt and .ifc downloads
```

STAAD.Pro and Revit run on student licences, on one workstation, for an academic demonstration only. Licensing
and public deployment are future work.

If the workstation is offline, the same Vercel site still runs the brief, standards and layout stages, and the
later pages show the saved demo run.

---

## A. One-time setup

### A1. pyRevit (Windows)
1. Install pyRevit 7.x (6.4 or later supports Revit 2027) from github.com/pyrevitlabs/pyRevit/releases.
2. Run `bash scripts/demo_up.sh` once from WSL. It copies `revit/` to `C:\Users\Public\Documents\STRATA\revit`.
3. In Revit, open pyRevit > Settings:
   - Custom extension directories: add `C:\Users\Public\Documents\STRATA\revit`
   - Routes: tick "Enable Routes server" (port 48884)
   - Save, then reload pyRevit.
4. Check: open `http://127.0.0.1:48884/strata/status` in a Windows browser. It should return
   `{"ok": true, ...}`. `template` stays false until A3.

### A2. Windows firewall
Routes listens on all interfaces. If Windows asks, allow Revit on **private networks only**. WSL reaches it through
localhost (mirrored networking), so nothing else needs to reach port 48884.

### A3. STRATA Revit template (the three families)
The structural families come from Autodesk's online library, which a script cannot download. Load them once and
save a template:
1. New project from `Default-Multi-Discipline_Metric`.
2. Insert > Load Autodesk Family: `M_Concrete-Rectangular-Column`, `M_Concrete-Rectangular Beam`,
   `M_Footing-Rectangular`.
3. File > Save As > Template: `C:\Users\Public\Documents\STRATA\revit\STRATA_template.rte`.
4. Check `http://127.0.0.1:48884/strata/status`: `template` should now be true.

### A4. ngrok (WSL)
1. Create a free account at ngrok.com and claim your free static domain (Domains page), for example
   `something.ngrok-free.app`.
2. Install the agent in WSL by following ngrok's Linux instructions (apt repository), then run
   `ngrok config add-authtoken <your token>` yourself.
3. Add to the repo's `.env`:
   ```
   STRATA_NGROK_DOMAIN=something.ngrok-free.app
   STRATA_ACCESS_CODE=<a code you give the audience>
   STRATA_ALLOWED_ORIGINS=https://<your-project>.vercel.app,http://localhost:3000
   ```

### A5. Vercel
In the Vercel project settings > Environment Variables:
- `NEXT_PUBLIC_STRATA_WORKER_URL` = `https://something.ngrok-free.app`
- `STRATA_ACCESS_CODE` = the same code (used by Vercel's own fallback functions)
- `GEMINI_API_KEY` and/or `GROQ_API_KEY`, and `STRATA_LLM_PROVIDER` if not Gemini (fallback functions only)

Then push and redeploy. `NEXT_PUBLIC_*` values are built into the page, so a change needs a redeploy.

---

## B. On demo day

1. Windows: start Revit 2027 and wait until pyRevit has loaded. No project needs to be open.
2. WSL: `bash scripts/demo_up.sh` and leave that terminal open. Every line under "4. Checks" should say OK.
3. Browser: open the Vercel site. The menu's status light should say **Workstation online** with STAAD.Pro and
   Revit **ready**. Click the light to check again after starting something.
4. Run the flow: Brief > Standards > Layout (approve) > Structure (live STAAD rounds) > Services > Building JSON >
   Revit model > **Build in Revit**. A two-storey house takes about a minute in Revit. Then download the .rvt and
   .ifc.

### Before presenting
- Do a full rehearsal run on the same network you will present on. Some campus networks block tunnels.
- Record a screen video of one full run as a backup.
- Keep Revit restored, not minimised during a build: Revit handles API requests only when it is idle and responsive.

## C. If something fails

| Symptom | Fix |
|---|---|
| Status light: Workstation offline | `demo_up.sh` not running, ngrok down, or the Vercel env var is wrong. Check `output/logs/ngrok.log`. |
| Revit: offline | Revit not started, pyRevit Routes disabled, or pyRevit not loaded yet. Check `http://127.0.0.1:48884/strata/status`. |
| Revit: no template | Step A3. |
| Build failed: "already has structural columns" | Cannot happen with the template flow (always a new project). If it does, the template itself has columns: save it again from an empty project. |
| Build hangs on "waiting for Revit" | A dialog is open in Revit, or Revit is busy. Close the dialog. The listener answers most dialogs itself. |
| Brief replies time out | The LLM free tier is slow or down: set `STRATA_LLM_PROVIDER=groq` in `.env` and restart `demo_up.sh`. |
| Tunnel blocked on campus | Run locally instead: `npm run dev` in `web/` with `demo_up.sh` running, and present from http://localhost:3000. |
| Everything else | Present the saved demo run (later pages show it automatically when the workstation is offline) and the recorded video. |
