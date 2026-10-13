import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'gezhi-cli-startup-'));
const guard = path.join(scratch, 'no-children.cjs');
fs.writeFileSync(guard, "const c=require('node:child_process'); c.spawn=()=>{throw Error('UNEXPECTED_CHILD_PROCESS')}; require('node:module').syncBuiltinESMExports();");
const run = (args, cli = path.join(root, 'src', 'cli.js')) => spawnSync(process.execPath, ['--require', guard, cli, ...args], {
  cwd: scratch, env: { ...process.env, GEZHI_DATA_DIR: path.join(scratch, 'data'), GEZHI_REMOTE_URL: '', GEZHI_REMOTE_TOKEN: '' },
  encoding: 'utf8', timeout: 5000,
});
try {
  for (const args of [['--help'], ['-h'], []]) {
    const result = run(args);
    assert.equal(result.status, 0, `${JSON.stringify(args)} must exit with friendly help; ${result.stderr}`);
    for (const text of ['--continue', '--print', '--version', '/models', '/setup']) assert.ok(result.stdout.includes(text), text);
    assert.ok(!/raw mode|UNEXPECTED_CHILD_PROCESS|引擎启动中/i.test(result.stdout + result.stderr));
  }
  const nonTty = run(['--continue']);
  assert.equal(nonTty.status, 1, 'resuming requires an interactive terminal');
  assert.ok(nonTty.stdout.includes('--print') || nonTty.stderr.includes('--print'));
  const unknown = run(['--not-a-real-option']);
  assert.equal(unknown.status, 1);
  assert.ok(unknown.stderr.includes('--help'));
  assert.ok(!unknown.stderr.includes('UNEXPECTED_CHILD_PROCESS'));
  assert.equal(fs.existsSync(path.join(scratch, 'data')), false, 'help/non-TTY/version must not initialize engine data');

  // Copy only the lightweight entry files: help/version must work before npm install.
  const isolated = path.join(scratch, 'without-ui-dependencies');
  fs.mkdirSync(path.join(isolated, 'src'), { recursive: true });
  for (const name of ['cli.js', 'headless.js', 'engine-select.js']) fs.copyFileSync(path.join(root, 'src', name), path.join(isolated, 'src', name));
  fs.copyFileSync(path.join(root, 'package.json'), path.join(isolated, 'package.json'));
  for (const args of [['--version'], ['--help']]) {
    const result = run(args, path.join(isolated, 'src', 'cli.js'));
    assert.equal(result.status, 0, `${args} cannot load React/Ink/App/Bridge: ${result.stderr}`);
  }
  const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
  const lock = JSON.parse(fs.readFileSync(path.join(root, 'package-lock.json'), 'utf8'));
  assert.equal(run(['--version']).stdout.trim(), `gezhi ${pkg.version}`);
  assert.equal(lock.version, pkg.version);
  assert.equal(lock.packages[''].version, pkg.version);
  console.log('CLI help, non-TTY safety, no engine/dependency imports, errors and version consistency passed');
} finally {
  fs.rmSync(scratch, { recursive: true, force: true });
}
