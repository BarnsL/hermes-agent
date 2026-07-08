/**
 * restart-counter.cjs — Strict, persistent restart & shutdown counter.
 * Logs every boot, every shutdown, and counts restarts accurately.
 *
 * Persisted to %LOCALAPPDATA%/hermes/restart-counts.json
 *
 * Called from main.cjs at:
 *   - app.whenReady → incrementBoot()
 *   - app.on('before-quit') → logShutdown('app.quit')
 *   - app.on('will-quit') → logShutdown('app.will-quit')
 *   - tray restart handler → incrementRestart()
 *   - process.on('exit') → logShutdown('process.exit')
 */

const fs = require('fs')
const path = require('path')

const COUNTS_FILE = path.join(
    process.env.LOCALAPPDATA || path.join(process.env.USERPROFILE || '', 'AppData', 'Local'),
    'hermes',
    'restart-counts.json'
)

function readCounts() {
    try {
        const raw = fs.readFileSync(COUNTS_FILE, 'utf-8')
        return JSON.parse(raw)
    } catch {
        return { boots: 0, shutdowns: 0, restarts: 0, events: [] }
    }
}

function writeCounts(counts) {
    try {
        const dir = path.dirname(COUNTS_FILE)
        if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true })
        fs.writeFileSync(COUNTS_FILE, JSON.stringify(counts, null, 2), 'utf-8')
    } catch (e) {
        // Best-effort — don't crash if we can't write
    }
}

function isoNow() {
    return new Date().toISOString()
}

function incrementBoot() {
    const counts = readCounts()
    counts.boots += 1
    counts.events.push({ type: 'boot', time: isoNow() })
    writeCounts(counts)
    return counts.boots
}

function logShutdown(reason) {
    const counts = readCounts()
    counts.shutdowns += 1
    counts.events.push({ type: 'shutdown', reason, time: isoNow() })
    writeCounts(counts)
}

function incrementRestart() {
    const counts = readCounts()
    counts.restarts += 1
    counts.events.push({ type: 'restart', time: isoNow() })
    writeCounts(counts)
    return counts.restarts
}

function getCounts() {
    return readCounts()
}

module.exports = { incrementBoot, logShutdown, incrementRestart, getCounts }
