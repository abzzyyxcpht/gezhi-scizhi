"""Build a local review gallery from native Flet screenshots and real Ink frames."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "design-previews/2026-10-03-agent-polish"


def main(out_dir=None, groups=("picker", "transitions"), comprehensive=False):
    global OUT
    if out_dir is not None:
        OUT = Path(out_dir)
    frames = []
    for group in groups:
        report = json.loads((OUT / "tui" / f"{group}.json").read_text(encoding="utf-8"))
        assert report["assertions_passed"] and not report["native_pty"]
        frames.extend(dict(frame, group=group, label=frame.get("label") or frame["id"]) for frame in report["frames"])
    captures = sorted((OUT / ("native" if comprehensive else "desktop")).glob("*/manifest.json"))
    if not captures:
        raise FileNotFoundError("Run capture_desktop.py with the documented gallery output first")
    directory = captures[-1].parent
    image_files = sorted(directory.glob("*.png")) + sorted((OUT / "native-advanced").glob("*/*.png"))
    images = [{"src": image.relative_to(OUT).as_posix(), "name": image.stem}
              for image in image_files]
    data = json.dumps({"frames": frames, "images": images}, ensure_ascii=False).replace("<", "\\u003c")
    document = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>格致 · 双端交互精修</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f6;color:#242b30;font:14px/1.65 system-ui,"Microsoft YaHei UI",sans-serif}
main{max-width:1380px;margin:0 auto;padding:32px 28px 60px}header{display:flex;justify-content:space-between;gap:24px;align-items:center;border-bottom:1px solid #d5dde0;padding-bottom:20px}
.brand{display:flex;gap:12px;align-items:center}.mark{background:#286557;color:white;padding:6px 12px;border-radius:7px;font:22px/1.5 Consolas,monospace}
.brand strong{font-size:19px}.small{font-size:12px;color:#56636b}h1{font-size:36px;letter-spacing:-1.2px;line-height:1.25;margin:30px 0 12px}p{margin:0 0 12px}a{color:#286557}button,select{font:inherit;border-radius:6px;border:1px solid #7a8a94;background:white;color:#242b30;padding:8px 14px;min-height:40px;cursor:pointer}button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid #286557;outline-offset:3px}
nav{display:flex;gap:8px;margin:24px 0}nav button[aria-pressed=true]{background:#286557;color:white;border-color:#286557}.overview{max-width:820px;color:#56636b}.review{display:grid;grid-template-columns:minmax(0,1fr) 270px;gap:24px;align-items:start}.panel{min-width:0;background:white;border:1px solid #dae1e5;border-radius:10px;overflow:hidden}.tools{padding:16px 20px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;border-bottom:1px solid #dae1e5}.tools select{min-width:0;max-width:100%;flex:1}pre{margin:0;padding:22px;background:#162027;color:#bdd2d4;font:14px/1.65 Consolas,"Cascadia Code",monospace;white-space:pre;overflow:auto;min-height:360px}#frameLabel{padding:14px 20px;color:#286557;border-top:1px solid #dae1e5}input[type=range]{accent-color:#286557;flex:1;min-width:100px}aside{padding-top:8px}aside h2{font-size:15px;margin:0 0 8px}aside p{font-size:13px;color:#56636b;margin-bottom:22px}.pill{display:inline-block;border:1px solid #c8d8d1;background:#e5eeea;color:#286557;border-radius:4px;padding:3px 7px;font-size:11px;letter-spacing:.4px}#nativeImage{width:100%;height:auto;display:block}#native{max-width:1200px}footer{margin-top:32px;color:#56636b;font-size:12px}.hidden{display:none!important}
@media(max-width:850px){main{padding:20px 16px 40px}.review{grid-template-columns:1fr}aside{display:grid;grid-template-columns:1fr 1fr;gap:16px}h1{font-size:28px}pre{font-size:12px;padding:14px}header{align-items:start}.tools{padding:12px}header>.small{max-width:130px}}@media(max-width:460px){aside{grid-template-columns:1fr}header>.small{display:none}.tools select{flex-basis:100%}}
</style></head><body><main>
<header><div class="brand"><span class="mark" aria-hidden="true">╭┬╮</span><div><strong>格致 SciZhi</strong><div class="small">科研工作台 · 双端交互精修</div></div></div><div class="small">2026.10.03<br>源码更新 · 0.8.0</div></header>
<h1>从问题出发，让证据落地。</h1><p class="overview">书页启动画面、键盘思考强度选择、明确的保存反馈，以及始终可见的桌面输入与执行入口。</p>
<nav aria-label="预览类型"><button id="terminalTab" aria-pressed="true">终端交互</button><button id="desktopTab" aria-pressed="false">桌面工作台</button></nav>
<section id="terminal" class="review"><div class="panel"><div class="tools"><select id="framePicker" aria-label="选择交互帧"></select><span id="sizeLabel" class="pill"></span></div><pre id="terminalFrame" aria-label="实际 Ink 渲染文本"></pre><div id="frameLabel" aria-live="polite"></div><div class="tools"><button id="play" aria-pressed="false">回放</button><input id="timeline" type="range" min="0" aria-label="回放帧"><span id="counter" class="small"></span></div></div><aside><div><h2>键盘选择，全档可达</h2><p>/reasoning 打开菜单。方向键预览，Enter 保存，Esc 取消；保存期间仍能编辑草稿。</p></div><div><h2>反馈来自真实回执</h2><p>连接与加载各有状态。保存确认后才播放刻度过渡，失败保留已保存值与输入。</p></div><div><h2>尺寸与状态均可复核</h2><p>这里回放的是实际 App / Ink 渲染文本，使用隔离的模拟 Bridge；不代表原生终端或供应商验收。</p><a href="../../docs/research/2026-10-03-agent-design-benchmark.md">查看开源对标来源</a></div></aside></section>
<section id="native" class="panel hidden"><div class="tools"><select id="imagePicker" aria-label="选择桌面截图"></select><span class="pill">原生 Flet 截图</span></div><img id="nativeImage" alt="格致原生桌面交互截图"></section>
<footer>使用源码启动即可体验本轮更新。已有安装包与源码发布包保留；截图使用独立离线样例，终端帧不调用真实模型。<a href="../../docs/agent-experience-polish-2026-10-03.md">使用与验证记录</a></footer>
</main><script id="evidence" type="application/json">__EVIDENCE__</script><script>
const evidence=JSON.parse(document.getElementById('evidence').textContent);
const picker=document.getElementById('framePicker'),timeline=document.getElementById('timeline');
let active=0,timer=null;
evidence.frames.forEach((frame,i)=>{const option=document.createElement('option');option.value=i;option.textContent=(frame.group==='picker'?'选择器 · ':'启动与过渡 · ')+frame.label;picker.appendChild(option)});
timeline.max=evidence.frames.length-1;
function showFrame(i){active=Number(i);const frame=evidence.frames[active];document.getElementById('terminalFrame').textContent=frame.text;document.getElementById('frameLabel').textContent=frame.label;document.getElementById('sizeLabel').textContent=frame.width+' × '+frame.height;document.getElementById('counter').textContent=(active+1)+' / '+evidence.frames.length;picker.value=active;timeline.value=active}
function stop(){clearInterval(timer);timer=null;document.getElementById('play').textContent='回放';document.getElementById('play').setAttribute('aria-pressed','false')}
picker.onchange=()=>{stop();showFrame(picker.value)};timeline.oninput=()=>{stop();showFrame(timeline.value)};
document.getElementById('play').onclick=()=>{if(timer){stop();return}document.getElementById('play').textContent='暂停';document.getElementById('play').setAttribute('aria-pressed','true');timer=setInterval(()=>{if(active===evidence.frames.length-1){stop();return}showFrame(active+1)},600)};
const imagePicker=document.getElementById('imagePicker');evidence.images.forEach((item,i)=>{const option=document.createElement('option');option.value=i;option.textContent=item.name;imagePicker.appendChild(option)});
function showImage(){document.getElementById('nativeImage').src=evidence.images[imagePicker.value||0].src}imagePicker.onchange=showImage;
function showTab(desktop){stop();document.getElementById('terminal').classList.toggle('hidden',desktop);document.getElementById('native').classList.toggle('hidden',!desktop);document.getElementById('terminalTab').setAttribute('aria-pressed',String(!desktop));document.getElementById('desktopTab').setAttribute('aria-pressed',String(desktop))}
document.getElementById('terminalTab').onclick=()=>showTab(false);document.getElementById('desktopTab').onclick=()=>showTab(true);showFrame(0);showImage();
</script></body></html>'''
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / "index.html"
    document = document.replace("__EVIDENCE__", data)
    if comprehensive:
        document = document.replace("双端交互精修", "ZCode / DeepSeek 综合升级")
        document = document.replace("书页启动画面、键盘思考强度选择、明确的保存反馈，以及始终可见的桌面输入与执行入口。", "命令搜索、阶段模型与思考选择、分层执行活动，以及可靠的配置保存和连接恢复。")
        document = document.replace("/reasoning 打开菜单。方向键预览，Enter 保存，Esc 取消；保存期间仍能编辑草稿。", "Ctrl+P 搜索命令，Enter 填入；/models 选择阶段、模型与思考强度。Esc 返回时草稿保留。")
        document = document.replace("../.." + "/docs/agent-experience-polish-2026-10-03.md", "../../docs/comprehensive-upgrade-2026-10-03.md")
        document = document.replace("../../docs/research/2026-10-03-agent-design-benchmark.md", "../../docs/research/2026-10-03-zcode-source-upgrade.md")
        document = document.replace("frame.group==='picker'?'选择器 · ':'启动与过渡 · '", "frame.group==='models'?'模型与思考 · ':'命令与执行活动 · '")
    destination.write_text(document, encoding="utf-8")
    print(json.dumps({"file": str(destination), "ink_frames": len(frames), "native_images": len(images)}))


if __name__ == "__main__":
    main()
