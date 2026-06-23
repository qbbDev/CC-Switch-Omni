const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    // Main window controls
    closeWindow:    () => ipcRenderer.send('window-close'),
    minimizeWindow: () => ipcRenderer.send('window-minimize'),

    // Open a card-specific floating widget
    openCardWidget: (cardId) => ipcRenderer.send('open-card-widget', cardId),

    // Card widget self-controls (used from widget.html)
    closeWidget:    () => ipcRenderer.send('widget-close'),
    minimizeWidget: () => ipcRenderer.send('widget-minimize'),

    // Resize the current widget window (used for collapse/expand)
    resizeWidget: (width, height) => ipcRenderer.send('widget-resize', { width, height }),

    // Persistent JSON configuration (shared across dev/prod and app launches)
    saveConfig: (config) => ipcRenderer.send('save-config', config),
    loadConfig: () => ipcRenderer.invoke('load-config'),
});
