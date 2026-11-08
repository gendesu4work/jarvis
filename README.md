# Jarvis

A personal voice assistant for Windows: say **"Jarvis"**, ask anything, and it answers out loud.
It runs on your own PC, opens your apps, uses its own Chrome, sees your screen when asked, controls Spotify,
keeps notes, and **asks before doing anything risky**.

> This repo is **private**: it contains your settings, notes and Jarvis's memory of you.
> Your API key is **never** stored here.

---

## Install on a new PC (Windows 10 2004+ / Windows 11)

1. **Download the installer**
   Open this repo on GitHub → **Releases** (right side) → **Jarvis (latest)** → download **`Jarvis-Setup.exe`**.
   *(It's rebuilt automatically every time this repo changes.)*

2. **Run `Jarvis-Setup.exe`**
   If Windows shows "Windows protected your PC", click **More info → Run anyway** (the app isn't code-signed).
   Click **Install**, leave **"Set up Jarvis"** ticked, click **Finish**.

3. **Let the setup window work** (15–30 min, mostly downloads). It will:
   - turn on **WSL** (Jarvis's safe sandbox) — Windows asks for permission, then **restarts once**;
     setup **continues by itself** after you sign back in
   - install voice recognition, Jarvis's voice and the "Jarvis" wake word
   - install Hermes Agent (Jarvis's brain)
   - restore **your settings, notes, memory, persona, theme and layout** from this repo

4. **Two small things for you**
   - **Ubuntu account:** a black Ubuntu window opens → type a simple lowercase username → type a password twice
     (nothing shows while typing — normal) → when you see a prompt ending in `$`, close the window.
   - **Gemini API key:** a "Connect Jarvis to Google Gemini" window opens:
     1. Click **Open Google AI Studio**
     2. Sign in with your Google account
     3. Click **Create API key**
     4. Click the copy icon next to the key (starts with `AIza…`)
     5. Paste it and click **Test & Save**

     You can use the **same key** on all your PCs (they share the same free daily limit).

5. **Done.** Jarvis opens full screen and starts automatically whenever you sign in.

### Start menu entries
| Entry | What it does |
|---|---|
| **Jarvis** | The app (voice + HUD) |
| **Jarvis Terminal** | Type to Jarvis; asks before risky commands |
| **Jarvis Sync Settings** | Keep settings/notes/memory the same on all PCs (below) |
| **Jarvis Setup (repair)** | Re-run setup (safe; skips what's done) — also to add/change the API key |

---

## Keep PCs in sync

On each PC, once: `winget install Git.Git GitHub.cli` then `gh auth login`.
Then click **Jarvis Sync Settings** whenever you want both PCs to match. It saves this PC's
settings to this repo, pulls the other PC's changes, and applies the merged result.

What syncs: notes, HUD settings/theme/layout, voice settings, Jarvis's memory and persona, scheduled jobs,
skills Jarvis created, model choice. What never syncs: the API key, screenshots, logs, browser profiles.

---

## Different hardware
Mic and speakers: Jarvis uses the names in `Documents\jarvis\hud\settings.json`
(`input_device` / `output_device`); if they aren't on this PC it uses **Windows's default** devices.
Monitor: `"monitor"` in `layout.json` (`left` / `right` / `main`); falls back to the main screen.
You can also just tell Jarvis: *"use my headset microphone"*, *"open on the main screen"*.

## Repo layout
```
app/          the program (voice engine, HUD, Windows bridge, helper scripts)
installer/    setup.ps1 (one-click setup), sync.ps1, Jarvis.iss (installer recipe)
profile/      your saved settings (no secrets)
.github/      builds Jarvis-Setup.exe into Releases on every push
```
