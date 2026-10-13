"""Build a local gallery of verified native captures and actual Ink replay frames.

Run capture_desktop.py and the TUI mode replay first. This script draws no app UI
and refuses missing or failed evidence instead of inventing screenshots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / 'design-previews/2026-10-04-dual-mode'
SCENES = {'general-workspace': '通用工作台', 'research-mode': '科研模式',
          'mode-commands': '模式命令搜索'}


def collect_evidence(out: Path, tui_path: Path) -> dict:
    replay = json.loads(tui_path.read_text(encoding='utf-8'))
    if replay.get('assertions_passed') is not True or not replay.get('frames'):
        raise ValueError('TUI replay must contain passed assertions and actual frames')
    frames = []
    for frame in replay['frames']:
        if not isinstance(frame.get('text'), str) or not frame['text'].strip():
            raise ValueError('Every TUI frame must contain nonempty actual render text')
        frames.append({key: frame.get(key) for key in ('id', 'label', 'text', 'width', 'height')})

    candidates = []
    for path in sorted((out / 'native').glob('*/manifest.json')):
        report = json.loads(path.read_text(encoding='utf-8'))
        present = {row.get('screen') for row in report.get('captures', [])}
        if report.get('status') == 'captured' and report.get('fixture') is True and set(SCENES) <= present:
            candidates.append((path, report))
    if not candidates:
        raise FileNotFoundError('Capture all three dual-mode scenes successfully under --out/native first')
    manifest, capture = candidates[-1]
    images = []
    for row in capture['captures']:
        if row.get('screen') not in SCENES:
            continue
        file = (manifest.parent / row['file']).resolve()
        if not file.is_relative_to(manifest.parent.resolve()) or file.suffix.lower() != '.png':
            raise ValueError('Capture image must be a PNG inside its native run directory')
        payload = file.read_bytes()
        if not payload.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError(f'Capture does not contain actual PNG bytes: {file.name}')
        interaction = row.get('interaction', {})
        if interaction.get('real_control_callbacks') is not True or interaction.get('saved_mode_readback') is not True:
            raise ValueError('Dual-mode capture must verify native callbacks and saved mode readback')
        images.append({'src': file.relative_to(out).as_posix(), 'name': SCENES[row['screen']],
            'screen': row['screen'], 'requested_size': row['requested_size'],
            'pixel_size': row['pixel_size'], 'mode': interaction['workspace_mode'],
            'draft_preserved': interaction.get('draft_preserved') is True,
            'sha256': hashlib.sha256(payload).hexdigest()})
    return {'images': images, 'frames': frames, 'native_pty': replay.get('native_pty') is True,
            'manifest': manifest.relative_to(out).as_posix(),
            'tui_report': Path(os.path.relpath(tui_path, out)).as_posix()}


DOCUMENT = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>格致 SciZhi · 通用与科研工作台</title><style>
:root{--bg:#f2f5f4;--surface:#fff;--ink:#173b36;--accent:#258b7b;--muted:#6b7779;--line:#d4dfdc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 "Microsoft YaHei UI",system-ui,sans-serif}
main{max-width:1480px;padding:28px 28px 42px;margin:auto}header{display:flex;align-items:center;justify-content:space-between;gap:20px;padding-bottom:18px;border-bottom:1px solid var(--line)}
header strong{font-size:20px;letter-spacing:.2px}.date,.caption{color:var(--muted);font-size:12px}h1{font-size:30px;font-weight:600;line-height:1.35;margin:24px 0 8px;letter-spacing:.3px}.intro{margin:0;max-width:840px;color:var(--muted)}
nav{display:flex;gap:6px;align-items:center;margin:22px 0 14px}button,select,a.open{font:inherit;min-height:40px;border:1px solid var(--line);background:var(--surface);color:var(--ink);padding:8px 12px;border-radius:6px;cursor:pointer}
button[aria-pressed=true]{background:var(--ink);color:#fff;border-color:var(--ink)}button:focus-visible,select:focus-visible,a:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:3px}a{color:var(--accent);text-underline-offset:3px}a.open{text-decoration:none;display:inline-flex;align-items:center}
.panel{border:1px solid var(--line);background:var(--surface);border-radius:9px;overflow:hidden}.tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:12px 16px;border-bottom:1px solid var(--line)}.tools select{min-width:180px;max-width:100%;flex:1}.tools .caption{margin-left:auto}.native-image{display:block;width:100%;height:auto}.facts{padding:12px 16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;display:flex;gap:14px;flex-wrap:wrap}.terminal{background:#17252a;color:#d6e5e2;padding:20px;min-height:360px;margin:0;overflow:auto;font:14px/1.65 "Cascadia Code",Consolas,"Microsoft YaHei UI",monospace;white-space:pre}.range{accent-color:var(--accent);flex:1;min-width:100px}.hidden{display:none!important}.note{display:flex;gap:28px;flex-wrap:wrap;margin-top:18px;padding:16px 0;border-bottom:1px solid var(--line)}.note div{flex:1;min-width:240px}.note strong{font-size:13px}.note p{margin:4px 0 0;font-size:12px;color:var(--muted)}footer{margin-top:16px;display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--muted)}
@media(max-width:700px){main{padding:18px 12px 30px}h1{font-size:25px}.date{max-width:110px;text-align:right}.tools{padding:10px}.tools select{flex-basis:100%}.tools .caption{margin-left:0}.terminal{font-size:12px;padding:12px}.note{gap:12px;margin-top:10px}.facts{gap:8px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
</style></head><body><main>
<header><strong>格致 SciZhi</strong><span class="date">2026.10.04 · 双模式源码预览</span></header>
<h1>通用任务与科研工作，随时切换。</h1>
<p class="intro">同一工作台中的两种用途：通用模式处理代码、文件与日常任务；科研模式组织文献、数据与证据。下面展示本轮实际桌面截图与终端交互回放。</p>
<nav aria-label="预览类型"><button id="desktopTab" aria-pressed="true">桌面截图</button><button id="terminalTab" aria-pressed="false">终端回放</button></nav>
<section id="desktop" class="panel" aria-label="原生桌面截图">
<div class="tools"><button id="previousImage" aria-label="上一张截图">←</button><select id="imagePicker" aria-label="选择截图"></select><button id="nextImage" aria-label="下一张截图">→</button><a class="open" id="openImage" target="_blank" rel="noopener">查看原图</a><span class="caption" id="imageCounter"></span></div>
<img id="nativeImage" class="native-image" alt="格致原生桌面工作台截图">
<div class="facts"><span id="imageSize"></span><span id="modeFact"></span><span id="draftFact"></span><a id="manifestLink">截图与控件回执记录</a></div></section>
<section id="terminal" class="panel hidden" aria-label="实际 Ink 回放">
<div class="tools"><button id="previousFrame" aria-label="上一帧">←</button><select id="framePicker" aria-label="选择终端帧"></select><button id="nextFrame" aria-label="下一帧">→</button><span class="caption" id="frameSize"></span></div>
<pre id="terminalFrame" class="terminal" aria-label="实际 App 与 Ink 渲染文本"></pre>
<div class="tools"><button id="play" aria-pressed="false">回放</button><input id="timeline" class="range" type="range" min="0" aria-label="终端回放帧"><span class="caption" id="frameCounter"></span></div>
<div class="facts"><span id="ptyFact"></span><span>通过隔离交互场景的断言</span><a id="tuiLink">完整回放记录</a></div></section>
<div class="note"><div><strong>切换后用于新会话</strong><p>模式按钮经保存回执更新。原会话沿用原角色，未发送草稿保留；执行方式与工具权限单独控制。</p></div><div><strong>命令可搜索</strong><p>桌面 Ctrl+K、终端 Ctrl+P 打开命令入口；/mode 也可直接切换。目录按用途组织并支持中文说明检索。</p></div><div><strong>这些预览证明的范围</strong><p>桌面使用独立离线数据和真实控件回调；Ink 使用实际组件的隔离场景。未验证真实模型、系统输入法或供应商吞吐。</p></div></div>
<footer><a id="researchLink">官方对标来源与差距说明</a><span>本轮为源码更新；既有安装包与发布归档分别验收。</span></footer>
</main><script id="evidence" type="application/json">__EVIDENCE__</script><script>
const evidence=JSON.parse(document.getElementById('evidence').textContent);
const byId=id=>document.getElementById(id);let imageIndex=0,frameIndex=0,timer=null;
function options(picker,rows,label){rows.forEach((row,i)=>{const option=document.createElement('option');option.value=i;option.textContent=label(row,i);picker.appendChild(option)})}
options(byId('imagePicker'),evidence.images,row=>row.name+' · '+row.requested_size.join(' × '));
options(byId('framePicker'),evidence.frames,row=>row.label||row.id);
function showImage(value){imageIndex=(Number(value)+evidence.images.length)%evidence.images.length;const row=evidence.images[imageIndex];byId('imagePicker').value=imageIndex;byId('nativeImage').src=row.src;byId('nativeImage').alt=row.name+' 原生 Flet 截图';byId('openImage').href=row.src;byId('imageCounter').textContent=(imageIndex+1)+' / '+evidence.images.length;byId('imageSize').textContent='实际像素 '+row.pixel_size.join(' × ');byId('modeFact').textContent='已保存：'+(row.mode==='general'?'通用':'科研');byId('draftFact').textContent=row.draft_preserved?'草稿保留已检查':'草稿未检查'}
function stop(){clearInterval(timer);timer=null;byId('play').textContent='回放';byId('play').setAttribute('aria-pressed','false')}
function showFrame(value){frameIndex=(Number(value)+evidence.frames.length)%evidence.frames.length;const row=evidence.frames[frameIndex];byId('framePicker').value=frameIndex;byId('timeline').value=frameIndex;byId('terminalFrame').textContent=row.text;byId('frameSize').textContent=row.width&&row.height?row.width+' × '+row.height:'Ink 帧';byId('frameCounter').textContent=(frameIndex+1)+' / '+evidence.frames.length}
byId('timeline').max=evidence.frames.length-1;
byId('imagePicker').onchange=e=>showImage(e.target.value);byId('previousImage').onclick=()=>showImage(imageIndex-1);byId('nextImage').onclick=()=>showImage(imageIndex+1);
byId('framePicker').onchange=e=>{stop();showFrame(e.target.value)};byId('previousFrame').onclick=()=>{stop();showFrame(frameIndex-1)};byId('nextFrame').onclick=()=>{stop();showFrame(frameIndex+1)};byId('timeline').oninput=e=>{stop();showFrame(e.target.value)};
byId('play').onclick=()=>{if(timer){stop();return}if(frameIndex===evidence.frames.length-1)showFrame(0);byId('play').textContent='暂停';byId('play').setAttribute('aria-pressed','true');timer=setInterval(()=>{if(frameIndex===evidence.frames.length-1){stop();return}showFrame(frameIndex+1)},650)};
function tab(desktop){stop();byId('desktop').classList.toggle('hidden',!desktop);byId('terminal').classList.toggle('hidden',desktop);byId('desktopTab').setAttribute('aria-pressed',String(desktop));byId('terminalTab').setAttribute('aria-pressed',String(!desktop))}
byId('desktopTab').onclick=()=>tab(true);byId('terminalTab').onclick=()=>tab(false);
byId('manifestLink').href=evidence.manifest;byId('tuiLink').href=evidence.tui_report;byId('researchLink').href=evidence.research;
byId('ptyFact').textContent=evidence.native_pty?'原生 PTY 场景':'非原生终端 · 实际 Ink 渲染';showImage(0);showFrame(0);
</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--tui', type=Path, help='Replay JSON; default is OUT/tui.json')
    args = parser.parse_args()
    out = args.out.resolve()
    evidence = collect_evidence(out, (args.tui or out / 'tui.json').resolve())
    research = ROOT / 'docs/research/2026-10-04-dual-mode-harness-benchmark.md'
    evidence['research'] = Path(os.path.relpath(research, out)).as_posix()
    serialized = json.dumps(evidence, ensure_ascii=False).replace('<', '\\u003c')
    out.mkdir(parents=True, exist_ok=True)
    destination = out / 'index.html'
    destination.write_text(DOCUMENT.replace('__EVIDENCE__', serialized), encoding='utf-8')
    print(json.dumps({'file': str(destination), 'native_images': len(evidence['images']),
                      'ink_frames': len(evidence['frames'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
