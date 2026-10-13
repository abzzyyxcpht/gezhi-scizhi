import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

/** Opens a local terminal without interpolating command input into a shell. */
export function openLocalTerminal({ platform = process.platform, launch = spawn, cwd = PROJECT_ROOT } = {}) {
  const [command, args] = platform === 'win32' ? ['powershell.exe', ['-NoLogo', '-NoExit']]
    : platform === 'darwin' ? ['open', ['-a', 'Terminal', cwd]]
      : ['x-terminal-emulator', []];
  return new Promise((resolve, reject) => {
    const child = launch(command, args, { cwd, detached: true, stdio: 'ignore', windowsHide: false, shell: false });
    child.once('error', () => reject(new Error('无法打开本机终端，请从系统启动终端')));
    child.once('spawn', () => { child.unref(); resolve(); });
  });
}
