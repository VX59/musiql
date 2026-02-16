const { app, BrowserWindow, globalShortcut } = require("electron");

let mainWindow;

app.whenReady().then(() => {
    mainWindow = new BrowserWindow({
        width: 1000,
        height: 700,
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
        }
    });

    mainWindow.loadURL("http://localhost:8000/musiql/player");
    
    globalShortcut.register("Control+F9", () => {
        mainWindow.webContents.executeJavaScript("window.skip()");
    });
});

app.on("will-quit", () => {
    globalShortcut.unregisterAll();
});
