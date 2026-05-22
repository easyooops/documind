import { spawnSync } from "node:child_process";
import net from "node:net";

const isWin = process.platform === "win32";

/**
 * Resolve a working Python executable (Windows: python / py / python3).
 * Override with PYTHON=py or PYTHON=python3.12
 */
export function resolvePython() {
  if (process.env.PYTHON) return process.env.PYTHON;

  const candidates = isWin
    ? ["python", "py", "python3"]
    : ["python3", "python"];

  for (const cmd of candidates) {
    const r = spawnSync(cmd, ["--version"], {
      stdio: "ignore",
      shell: isWin,
    });
    if (r.status === 0) return cmd;
  }

  throw new Error(
    "Python not found. Install Python 3.11+ and ensure it is on PATH, or set PYTHON (e.g. PYTHON=py on Windows)."
  );
}

export const platform = {
  isWin,
  isMac: process.platform === "darwin",
  isLinux: process.platform === "linux",
};

/** Stop a process by PID (force, with tree kill on Windows). */
export function killProcess(pid) {
  if (isWin) {
    spawnSync("taskkill", ["/PID", String(pid), "/F", "/T"], {
      shell: true,
      stdio: "ignore",
    });
  } else {
    spawnSync("kill", ["-9", String(pid)], { stdio: "ignore" });
  }
}

function sleepMs(ms = 1500) {
  if (isWin) {
    spawnSync("powershell", ["-NoProfile", "-Command", `Start-Sleep -Milliseconds ${ms}`], {
      stdio: "ignore",
    });
  } else {
    const secs = Math.max(1, Math.ceil(ms / 1000));
    spawnSync("sleep", [String(secs)], { stdio: "ignore" });
  }
}

/** Return true if a process with this PID is running. */
export function processExists(pid) {
  if (isWin) {
    const r = spawnSync("tasklist", ["/FI", `PID eq ${pid}`], {
      encoding: "utf8",
      shell: true,
    });
    return r.stdout?.includes(String(pid)) ?? false;
  }
  return spawnSync("kill", ["-0", String(pid)], { stdio: "ignore" }).status === 0;
}

/** True if a new server can bind to this port (checks both IPv4 and IPv6). */
export function isPortAvailable(port) {
  if (isWin) {
    const r = spawnSync(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        `Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count`,
      ],
      { encoding: "utf8" }
    );
    const count = parseInt(r.stdout?.trim(), 10);
    if (count > 0) return Promise.resolve(false);

    const nr = spawnSync("cmd", ["/c", `netstat -ano | findstr LISTENING`], {
      encoding: "utf8",
      shell: false,
    });
    if (nr.stdout) {
      const lines = nr.stdout.trim().split(/\r?\n/);
      for (const line of lines) {
        const portMatch = line.match(/:(\d+)\s+/);
        if (portMatch && parseInt(portMatch[1], 10) === port) {
          return Promise.resolve(false);
        }
      }
    }
    return Promise.resolve(true);
  }

  return new Promise((resolve) => {
    const tester4 = net.createServer();
    tester4.unref();
    tester4.once("error", () => resolve(false));
    tester4.once("listening", () => {
      tester4.close(() => {
        const tester6 = net.createServer();
        tester6.unref();
        tester6.once("error", () => resolve(false));
        tester6.once("listening", () => tester6.close(() => resolve(true)));
        tester6.listen({ port, host: "::", exclusive: true });
      });
    });
    tester4.listen({ port, host: "0.0.0.0", exclusive: true });
  });
}

/**
 * Stop old listener if possible, then return a port that can actually be bound.
 */
export async function resolveDevPort(label, preferred) {
  await tryFreePort(label, preferred);

  for (let port = preferred; port < preferred + 20; port++) {
    if (await isPortAvailable(port)) {
      if (port !== preferred) {
        console.log(
          `[documind] ${label}: port ${preferred} is busy; using ${port} instead.`
        );
      }
      return port;
    }
  }

  console.error(`\n[documind] No free port found for ${label} (${preferred}-${preferred + 19}).\n`);
  process.exit(1);
}

/**
 * Try to free a port (stop listener or wait for OS release).
 */
export async function tryFreePort(label, port) {
  if (await isPortAvailable(port)) return true;

  // Aggressively kill all processes on this port
  const pids = getAllListeningPids(port);
  if (pids.length > 0) {
    console.log(`[documind] Stopping previous ${label} on port ${port} (PIDs: ${pids.join(", ")})...`);
    for (const pid of pids) {
      killProcess(pid);
    }
  }

  // Windows: also try PowerShell-based kill as a belt-and-suspenders approach
  if (isWin) {
    spawnSync("powershell", [
      "-NoProfile",
      "-Command",
      `Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }`,
    ], { stdio: "ignore" });
  }

  for (let attempt = 0; attempt < 10; attempt++) {
    sleepMs();
    if (await isPortAvailable(port)) return true;

    const retryPids = getAllListeningPids(port);
    if (retryPids.length > 0) {
      for (const pid of retryPids) {
        killProcess(pid);
      }
    }
  }

  const available = await isPortAvailable(port);
  if (!available) {
    console.error(`[documind] WARNING: Could not free port ${port} for ${label}. Trying to continue anyway...`);
  }
  return available;
}

/** @deprecated Use tryFreePort; kept for scripts that only need cleanup on one port. */
export async function freePort(label, port) {
  if (!(await tryFreePort(label, port))) {
    const still = getListeningPid(port);
    console.error(
      `\n[documind] Port ${port} is still in use${still ? ` (PID ${still})` : ""}.\n` +
        (isWin && still ? `  Try: taskkill /PID ${still} /F /T\n` : "") +
        `  Or run npm run dev (auto-fallback port) / npm run dev:kill\n`
    );
    process.exit(1);
  }
}

/** Return PID listening on port, or null if port is free. */
export function getListeningPid(port) {
  const pids = getAllListeningPids(port);
  return pids.length ? pids[0] : null;
}

/** Return all PIDs using a port (LISTEN or any state). */
export function getAllListeningPids(port) {
  try {
    if (isWin) {
      // Try PowerShell Get-NetTCPConnection first
      const r = spawnSync(
        "powershell",
        [
          "-NoProfile",
          "-Command",
          `Get-NetTCPConnection -LocalPort ${port} -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique | Where-Object { $_ -ne 0 } | ForEach-Object { Write-Output $_ }`,
        ],
        { encoding: "utf8" }
      );
      let pids = r.stdout?.trim().split(/\r?\n/).filter(p => /^\d+$/.test(p)) ?? [];

      // Fallback to netstat if PowerShell found nothing
      if (pids.length === 0) {
        const nr = spawnSync("cmd", ["/c", `netstat -ano | findstr LISTENING`], {
          encoding: "utf8",
          shell: false,
        });
        if (nr.stdout) {
          const lines = nr.stdout.trim().split(/\r?\n/);
          const pidSet = new Set();
          for (const line of lines) {
            const portMatch = line.match(/:(\d+)\s+/);
            if (portMatch && parseInt(portMatch[1], 10) === port) {
              const pidMatch = line.match(/\s+(\d+)\s*$/);
              if (pidMatch && pidMatch[1] !== "0") pidSet.add(pidMatch[1]);
            }
          }
          pids = [...pidSet];
        }
      }
      return pids;
    }

    const r = spawnSync("lsof", ["-i", `:${port}`, "-t"], {
      encoding: "utf8",
    });
    if (r.status !== 0 || !r.stdout?.trim()) return [];
    return r.stdout.trim().split(/\r?\n/).filter(Boolean);
  } catch {
    return [];
  }
}

/**
 * Kill all processes using a port (both LISTEN and ESTABLISHED connections).
 * Critical on Windows where zombie connections can block new servers.
 */
export function killAllOnPort(port) {
  if (!isWin) return;
  const r = spawnSync(
    "powershell",
    [
      "-NoProfile",
      "-Command",
      `Get-NetTCPConnection -LocalPort ${port} -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique | ForEach-Object { taskkill /PID $_ /F /T 2>$null }`,
    ],
    { encoding: "utf8", shell: true }
  );
}
