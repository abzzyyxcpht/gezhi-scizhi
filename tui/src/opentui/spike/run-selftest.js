// 真 renderer 自检运行器（管道环境下验证原生渲染管线；截图/真控制台体验见 GUI 步骤）
// 运行: node src/opentui/spike/run-selftest.js
import { run } from "../runtime.js"

process.env.GEZHI_TUI_SELFTEST = process.env.GEZHI_TUI_SELFTEST || "C:\\Windows\\Temp\\scizhi-opentui-selftest.json"
const renderer = await run({ stdin: process.stdin, stdout: process.stdout })
if (!renderer) process.exit(1)
