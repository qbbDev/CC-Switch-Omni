const { app, BrowserWindow, screen, ipcMain, nativeImage } = require('electron');
const path = require('path');
const url  = require('url');
const { spawn, execSync } = require('child_process');
const fs = require('fs');

let mainWindow;
const cardWindows = new Map();   // cardId → BrowserWindow
let agentProcess = null;

// Find absolute path to python3 in a robust way for GUI launchd/Finder environments
function getPythonPath() {
    // 1. Try common absolute paths first
    const commonPaths = [
        '/opt/homebrew/bin/python3',
        '/usr/local/bin/python3',
        '/usr/bin/python3'
    ];
    for (const p of commonPaths) {
        if (fs.existsSync(p)) {
            return p;
        }
    }

    // 2. Fall back to which python3 in PATH
    try {
        const path = execSync('which python3').toString().trim();
        if (path) return path;
    } catch (e) {}

    return 'python3'; // System fallback
}

function startAgent() {
    const agentPath = path.join(__dirname, 'agent.py');
    const pythonPath = getPythonPath();

    // Create log file in ~/.cc-switch/agent.log
    const logDir = path.join(app.getPath('home'), '.cc-switch');
    if (!fs.existsSync(logDir)) {
        fs.mkdirSync(logDir, { recursive: true });
    }
    const logPath = path.join(logDir, 'agent.log');
    const logStream = fs.createWriteStream(logPath, { flags: 'a' });

    logStream.write(`\n[${new Date().toISOString()}] --- Spawning agent.py ---\n`);
    logStream.write(`Python Path: ${pythonPath}\n`);
    logStream.write(`Agent Path: ${agentPath}\n`);
    logStream.write(`Working Dir: ${__dirname}\n`);

    agentProcess = spawn(pythonPath, [agentPath], {
        cwd: __dirname,
        env: { ...process.env, PYTHONUNBUFFERED: '1' }
    });

    agentProcess.stdout.pipe(logStream);
    agentProcess.stderr.pipe(logStream);

    agentProcess.on('error', (err) => {
        logStream.write(`[ERROR] Failed to start agent process: ${err.message}\n`);
    });

    agentProcess.on('exit', (code, signal) => {
        logStream.write(`[EXIT] agent.py exited with code ${code} and signal ${signal}\n`);
        agentProcess = null;
    });
}


// ── Main Application Window ────────────────────────────────────
function createMainWindow() {
    const iconPath = path.join(__dirname, 'build', 'icon.png');

    mainWindow = new BrowserWindow({
        width:  1280,
        height: 820,
        minWidth:  900,
        minHeight: 600,
        // Native macOS title bar with traffic lights, content flows under it
        titleBarStyle: 'hiddenInset',
        trafficLightPosition: { x: 16, y: 18 },
        backgroundColor: '#090a0f',
        icon: iconPath,
        show: false,          // avoid flash on load
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            nodeIntegration: false,
            contextIsolation: true
        }
    });

    // Load with ?mode=app so index.html knows it's the main window
    const mainUrl = url.format({
        pathname: path.join(__dirname, 'index.html'),
        protocol: 'file:',
        slashes: true,
        query: { mode: 'app' }
    });
    mainWindow.loadURL(mainUrl);

    // Show once DOM is ready (prevents blank flash)
    mainWindow.once('ready-to-show', () => {
        mainWindow.show();
    });

    mainWindow.on('closed', () => {
        mainWindow = null;
    });
}

// ── Card-Specific Floating Widget ──────────────────────────────
function openCardWidget(cardId) {
    if (cardWindows.has(cardId)) {
        const existing = cardWindows.get(cardId);
        if (!existing.isDestroyed()) { existing.focus(); return; }
        cardWindows.delete(cardId);
    }

    const { width } = screen.getPrimaryDisplay().workAreaSize;
    const offset = cardWindows.size * 20;

    const win = new BrowserWindow({
        width: 360,
        height: 290,
        x: width - 385 - offset,
        y: 80 + offset,
        frame: false,
        transparent: true,
        alwaysOnTop: true,
        resizable: true,
        hasShadow: true,
        minWidth: 260,
        minHeight: 30,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            nodeIntegration: false,
            contextIsolation: true
        }
    });

    const widgetUrl = url.format({
        pathname: path.join(__dirname, 'widget.html'),
        protocol: 'file:',
        slashes: true,
        query: { card: cardId }
    });
    win.loadURL(widgetUrl);
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

    win.on('closed', () => { cardWindows.delete(cardId); });
    cardWindows.set(cardId, win);
}

// ── App Lifecycle ──────────────────────────────────────────────
app.whenReady().then(() => {
    startAgent();
    createMainWindow();
    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
        else if (mainWindow) mainWindow.show();
    });
});

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
});

app.on('will-quit', () => {
    if (agentProcess) {
        agentProcess.kill();
    }
});

// ── IPC Handlers ───────────────────────────────────────────────
ipcMain.on('window-close', () => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.close();
});

ipcMain.on('window-minimize', () => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.minimize();
});

ipcMain.on('open-card-widget', (event, cardId) => {
    openCardWidget(cardId);
});

ipcMain.on('widget-close', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win && !win.isDestroyed()) win.close();
});

ipcMain.on('widget-minimize', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win && !win.isDestroyed()) win.minimize();
});

ipcMain.on('widget-resize', (event, { width, height }) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win && !win.isDestroyed()) {
        win.setSize(Math.round(width), Math.round(height), true);
    }
});

// ── Persistent App Config Handlers ─────────────────────────────
const configDir = path.join(app.getPath('home'), '.cc-switch');
const configPath = path.join(configDir, 'config.json');

ipcMain.on('save-config', (event, config) => {
    try {
        if (!fs.existsSync(configDir)) {
            fs.mkdirSync(configDir, { recursive: true });
        }
        fs.writeFileSync(configPath, JSON.stringify(config, null, 2), 'utf8');
    } catch (e) {
        console.error('Failed to save config:', e);
    }
});

ipcMain.handle('load-config', async () => {
    try {
        if (fs.existsSync(configPath)) {
            const data = fs.readFileSync(configPath, 'utf8');
            return JSON.parse(data);
        }
    } catch (e) {
        console.error('Failed to load config:', e);
    }
    return null;
});
