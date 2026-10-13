// 夹具失败必须销毁真实 OpenTUI renderer，不能把断言失败变成保活进程。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

const script = `
  import { upgradeFixture } from './test/upgrade-ui-fixture.js';
  await upgradeFixture(async () => { throw new Error('intentional fixture failure'); })
    .catch(error => {
      if (error.message !== 'intentional fixture failure') throw error;
      console.log('fixture failure propagated');
    });
`;
const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
  cwd: new URL('..', import.meta.url), encoding: 'utf8', timeout: 10000,
});
assert.equal(result.error, undefined, `failed fixture must exit without live renderer timers: ${result.error?.message}`);
assert.equal(result.status, 0, result.stderr.slice(-2000));
assert.match(result.stdout, /fixture failure propagated/);
console.log('Upgrade UI fixture failure cleanup: passed');
