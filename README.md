# Void Cut

> Turn long videos into short clips with AI-assisted moment selection, an editing studio, and export tools.

Void Cut is developed from [Cheat Clip PRO](https://github.com/galihjuansaputra/cheat-clip-pro) by **galihjuansaputra and contributors**. It adds a dedicated workflow, a warm interface, subtitle editing, render recovery, and social account integration. See [CREDITS.md](CREDITS.md) for attribution.

The npm project name is `void-cut`. This checkout currently remains in `C:\Projects\cheat-clip-pro` so existing launchers, Python environments, and automation paths keep working. The folder name does not determine the product name or OAuth callback URLs.

---

## ⚡ Quick Start & Setup

Follow these steps to set up and run Void Cut locally on **Windows**, **macOS**, or **Linux**.

### Step 1: Prerequisites

Make sure you have the following installed:

1. **[Git](https://git-scm.com/)**
   * **Windows:** `winget install Git.Git` or download from [git-scm.com](https://git-scm.com/)
   * **macOS:** `brew install git`
   * **Linux:** `sudo apt install git`
2. **[Node.js](https://nodejs.org/)** (v18 or newer)
   * Download from [nodejs.org](https://nodejs.org/) or install via your package manager.
3. **[Python](https://www.python.org/)** (v3.10 or newer)
   * **Windows:** Install from [python.org](https://www.python.org/downloads/) (make sure to check *"Add Python to PATH"* during setup) or from Microsoft Store.
   * **macOS:** `brew install python`
   * **Linux:** `sudo apt install python3 python3-pip python3-venv`
4. **FFmpeg & yt-dlp** (Required to download, slice, and render clips)
   * **Windows (PowerShell):**
     ```powershell
     winget install Gyan.FFmpeg
     winget install yt-dlp.yt-dlp
     ```
     *(Close and reopen your terminal after installing so Windows recognizes them)*
   * **macOS (Terminal):**
     ```bash
     brew install ffmpeg-full yt-dlp
     ```
      `ffmpeg-full` is required for the libass subtitle filter used by rendered captions.
   * **Linux:**
     ```bash
     sudo apt update && sudo apt install ffmpeg
     pip install yt-dlp
     ```

---

### Step 2: Open the Project

For the existing Windows checkout, open PowerShell in the project directory:

```powershell
Set-Location C:\Projects\cheat-clip-pro
```

The upstream repository linked above is the original project. It does not include the local Void Cut additions. A dedicated Void Cut remote has not been configured yet.

---

### Step 3: Install Dependencies

#### 1. Frontend Dependencies
```bash
npm install
```

#### 2. Backend Dependencies
We recommend setting up a Python virtual environment:

* **Windows:**
  ```powershell
  python -m venv venv
  venv\Scripts\activate
  pip install -r backend/requirements.txt
  ```

* **macOS / Linux:**
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  pip install -r backend/requirements.txt
  ```

---

### Step 4: Run the App

Launch both the frontend and backend servers concurrently:

```bash
npm run dev
```

* **Web App:** [`http://localhost:5173`](http://localhost:5173)
* **Backend API:** [`http://localhost:8000`](http://localhost:8000)
* **API Documentation:** [`http://localhost:8000/docs`](http://localhost:8000/docs)

---

## 🔄 Updating This Checkout

This checkout's `origin` still points to Cheat Clip PRO. The inherited updater and `update.bat` pull from that remote; they are not a Void Cut release channel. Review upstream changes before applying them to this customized checkout.

After deliberately applying reviewed source changes, refresh dependencies:

```bash
npm install
pip install -r backend/requirements.txt
```
*(Make sure your virtual environment is activated if you created one)*

---

## 🔑 Free Google Gemini API Key (Takes 1 Minute)

Void Cut uses Google's AI to suggest clip candidates:
1. Go to **[Google AI Studio](https://aistudio.google.com/)** and sign in with any Google account.
2. Click **"Get API key"** (or **"Create API key"**).
3. Copy your key (starts with `AIzaSy...`).
4. Paste it into the **Gemini API Key** field in the app.

> 💡 **Tip:** You can also type `mock` in the API Key box to test out the app with sample data without an API key!

---

## 🎯 How to Use

1. **Paste a YouTube URL** — Enter any podcast, stream, or video link.
2. **Choose Duration** — Pick `~15s` (fast hooks), `~30s` (standard shorts), or `~60s` (story clips).
3. **Click "Find Clips" / "Cari Klip"** — After analysis, the app opens **Choose clips / Pilih klip**. Preview and select your clips, then click **Open Studio / Buka Studio**.
4. **Customize in Void Cut Studio** — Adjust your video style:
   * **Frame & Crop**: Fullscreen 9:16 vertical, square, or split-screen facecam.
   * **Face Tracking**: Automatically keeps the speaker in the center of the frame.
   * **Subtitles**: Choose viral animated karaoke caption styles and fonts.
   * **Branding & Audio**: Add your watermark logo, background music, and hook sound effects.
   * **Hardware Acceleration**: Choose your graphics card (NVIDIA, AMD, Intel) or CPU.
5. **Render & Export** — Click **Batch Render** to open **Exports / Ekspor**, track progress, and download available MP4 or ZIP files. Server renders also support connecting social accounts and reviewing uploads; see [the social upload setup guide](docs/social-upload-setup.md).

---

## ❓ Common Problems & Easy Fixes

### 1. "Failed to render video" or `The system cannot find the file specified`
* **Cause:** `ffmpeg` or `yt-dlp` is missing on your computer.
* **Fix:**
  * **Windows (PowerShell):**
    ```powershell
    winget install Gyan.FFmpeg
    winget install yt-dlp.yt-dlp
    ```
    *(Then close and reopen your terminal)*
  * **Mac (Terminal):**
    ```bash
    brew install ffmpeg-full yt-dlp
    ```
  * Or install directly via Python: `pip install yt-dlp`

### 2. `No such filter: 'subtitles'`
* **Cause:** FFmpeg was installed without the libass subtitle filter.
* **Fix (macOS):**
  ```bash
  brew install ffmpeg-full
  ```
  Restart the backend after installation. The app automatically prefers Homebrew's subtitle-capable `ffmpeg-full` binary.

### 3. "Sign in to confirm you're not a bot"
* **Cause:** YouTube blocks video downloads if too many requests are sent without logging in.
* **Fix:** Click the 🍪 **Cookies** button in the top navigation bar, export your YouTube cookies using a free browser extension (like *Get cookies.txt locally*), and paste them into the app.

### 4. Does this work on AMD graphics cards and Mac?
* **Yes!** Void Cut supports:
  * **NVIDIA** (`h264_nvenc`)
  * **AMD** (`h264_amf` on Radeon GPUs & Ryzen CPUs)
  * **Intel** (`h264_qsv` on Arc & UHD Graphics)
  * **Apple Mac & CPU Software** (`libx264` universal high-speed fallback)
* You can switch your preferred hardware acceleration encoder anytime in the Render Settings or History card.

---

## 📄 License

The original Cheat Clip PRO README declares the **MIT License**. Upstream attribution is retained in [CREDITS.md](CREDITS.md) and in the app footer. The upstream root currently has no standalone `LICENSE` file; the original copyright and license notice still need to be confirmed before distributing this derivative.
