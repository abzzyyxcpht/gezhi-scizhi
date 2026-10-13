// Replay verified real Ink text frames. This is not a native terminal capture.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { captureReasoningAppFrames } from '../test/reasoning-app-ui.js';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(project, 'design-previews/tui-interaction-2026-09-28');
const result = await captureReasoningAppFrames();
fs.mkdirSync(output, { recursive: true });
fs.writeFileSync(path.join(output, 'frames.json'), JSON.stringify(result, null, 2) + '\n');
const payload = JSON.stringify(result.frames).replaceAll('<', '\\u003c');
fs.writeFileSync(path.join(output, 'index.html'), `<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>格致交互预览 · mock Bridge + 真实 Ink · 非 PTY</title>
<style>
:root{color-scheme:dark;--ink:#d7e3e8;--dim:#8c9ea9;--line:#33444c;--green:#7bc7b8;--panel:#142129}
*{box-sizing:border-box}body{margin:0;background:#0e171e;color:var(--ink);font:15px/1.55 system-ui,sans-serif}
main{max-width:1200px;margin:48px auto;padding:0 28px}.brand{color:var(--green);letter-spacing:.12em;font-size:12px}
h1{font-size:30px;font-weight:550;margin:8px 0}p{color:var(--dim);max-width:820px;margin:8px 0 24px}
.controls{display:flex;gap:10px;flex-wrap:wrap;margin:28px 0 18px}button,select{font:inherit;padding:9px 13px;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:7px}
button{cursor:pointer}button:hover{border-color:var(--green)}button:focus-visible,select:focus-visible{outline:2px solid var(--green);outline-offset:3px}
.terminal{border:1px solid var(--line);background:#10191f;border-radius:10px;overflow:auto}.top{display:flex;justify-content:space-between;gap:16px;padding:12px 18px;border-bottom:1px solid var(--line);color:var(--dim);font-size:12px}
pre{margin:0;padding:18px;min-height:440px;white-space:pre;font:14px/1.35 Consolas,'Cascadia Mono','Microsoft YaHei',monospace}
.label{margin:16px 0;color:var(--ink)}.meta{font-size:12px;color:var(--dim)}footer{margin-top:26px;border-top:1px solid var(--line);padding-top:18px;color:var(--dim);font-size:13px}
@media(max-width:600px){main{padding:0 16px;margin:24px auto}h1{font-size:24px}pre{font-size:12px}.controls>*{flex:1 1 auto}}
</style>
<main><div class="brand">╭┬╮ · 格致 / 交互验证</div><h1>启动与思考强度</h1>
<p>80 × 24 的真实 App / Ink 渲染帧。后端使用 mock Bridge，保存成功、失败和等待由测试控制；不是原生 PTY、真实模型调用或启动性能测量。</p>
<div class="controls"><select id="pick" aria-label="选择验证状态"></select><button id="startup">播放启动采样</button><button id="reasoning">播放强度切换</button><button id="stop">停止</button></div>
<div class="terminal"><div class="top"><span>GEZHI · 80 × 24</span><span>mock Bridge + 真实 Ink · 非 PTY</span></div><pre id="screen" aria-label="终端渲染帧"></pre></div>
<div id="label" class="label" aria-live="polite"></div><div id="meta" class="meta"></div>
<footer>采样只在连接等待时出现，首次输入即定格。保存确认后才显示强度过渡，失败保留旧值；关闭动画时保留静态确认。浏览器按所选字体重放已验证文本，不等同于操作系统终端字体截图。</footer></main>
<script>
const frames=${payload};const pick=document.querySelector('#pick');let timer;
for(const [i,frame]of frames.entries())pick.add(new Option(frame.label,i));
function show(i){pick.value=i;document.querySelector('#screen').textContent=frames[i].text;document.querySelector('#label').textContent=frames[i].label;document.querySelector('#meta').textContent=frames[i].id+' · 测试场景采样 '+frames[i].elapsed_ms+' ms（非启动基准）';}
function stop(){clearTimeout(timer);timer=undefined;}function play(ids,delay){stop();let pos=0;const advance=()=>{show(ids[pos++]);if(pos<ids.length)timer=setTimeout(advance,delay);};advance();}
pick.onchange=()=>{stop();show(Number(pick.value));};document.querySelector('#stop').onclick=stop;
document.querySelector('#startup').onclick=()=>play(frames.map((frame,i)=>frame.id.startsWith('02-')?i:-1).filter(i=>i>=0),200);
document.querySelector('#reasoning').onclick=()=>play(frames.map((frame,i)=>/^(07|08|09)-/.test(frame.id)?i:-1).filter(i=>i>=0),360);
show(0);
</script></html>`);
console.log(`Verified interaction preview: ${path.join(output, 'index.html')}`);
