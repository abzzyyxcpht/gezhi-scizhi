# SciZhi Agent UI 对齐 ZCode 设计方案

## 目标

让 SciZhi 的 Flet 桌面端与 OpenTUI 终端端共享一套可维护的界面契约，全面对齐 ZCode 在字体、显示密度、主题层级、设置中心、侧栏布局、键盘焦点和动效节奏上的产品标准，同时保留 SciZhi 的科研工作流、证据账本和权限审批行为。

## 参考边界

对齐 ZCode 的公开设计规则与交互契约，不复制 ZCode 的业务代码或品牌资源。参考依据为 [zai-org/ZCode](https://github.com/zai-org/ZCode/tree/aac4755666d09fdcd70272fcf063c077a639015f) 3.15.1（Apache-2.0），固定提交 `aac4755666d09fdcd70272fcf063c077a639015f`。本地参考副本已移除，以下是原参考路径；现可通过根目录 DESIGN.md 中的链接复查：

- `zcode/ZCode-main/ZCode-main/DESIGN.md`
- `zcode/ZCode-main/ZCode-main/docs/ui/ui-font-tokens.md`
- `zcode/ZCode-main/ZCode-main/docs/ui/default-theme.md`
- `zcode/ZCode-main/ZCode-main/docs/ui/settings-appearance.md`
- `zcode/ZCode-main/ZCode-main/apps/zcode-cli/packages/tui/src/theme/`

## 设计契约

### 字体

- UI 默认字号为 14px，语义角色为 18 / 16 / 14 / 13 / 12 / 10 / 9。
- 界面字号只改变文字 token，不改变图标、间距和控件几何。
- UI 正文使用系统中文无衬线栈；路径、命令、模型 ID、哈希和代码使用等宽字体。
- 代码预览与终端字号独立于界面字号。
- Flet 和 TUI 使用同名角色：`ui_xl`、`ui_lg`、`ui_base`、`ui_caption`、`ui_sm`、`ui_xs`。

### 颜色与层级

- 使用语义 token，不在组件中写一次性颜色。
- 页面背景、卡片、普通 surface、输入框、弹窗、菜单、选中态分别有独立角色。
- 品牌色只用于主要操作、链接、当前状态和关键焦点；不铺满页面。
- 成功、警告、错误、等待确认、diff 等颜色只表达真实状态。
- 明暗主题和系统主题必须共享同一语义字段，并通过对比度测试。

### 几何与密度

- 4px 基准间距，优先使用 4 / 8 / 12 / 16 / 24 / 32。
- 普通控件高度收敛到 28 / 32 / 36px；菜单项和列表行保持紧凑。
- 圆角按层级统一：控件小圆角、卡片中圆角、弹窗大圆角；不再散落使用 6/8/10 等任意值。
- 桌面端保持高信息密度，科研长会话优先保证可读性和少滚动。

### 设置中心

统一桌面端与 TUI 的设置字段：

- `theme_mode`: `system` / `light` / `dark`
- `ui_font_size`: 12–20px，默认 14px
- `code_font_size`: 12–20px，独立于 UI 字号
- `code_light_theme` / `code_dark_theme`
- `code_show_line_numbers` / `code_wrap_long_lines`
- `density`: `compact` / `comfortable`
- `ui_animations`: 布尔值，关闭时所有非必要动效立即静止
- `sidebar_mode`: `docked` / `overlay` / `hidden`
- `inspector_visible`: 布尔值

旧字段继续读取并迁移，避免已有用户设置失效。

## 分阶段实施

### P0：共享 token 与设置契约

修改 `src/ui/theme.py`、`src/settings.py`、`tui/src/opentui/theme/tokens.js`、`tui/src/opentui/ui-type.js` 和设置命令，使 Flet/TUI 能读取同一套主题、字号、密度和动效字段。补齐 TUI 的 Markdown、代码、diff、链接、heading 和 syntax 语义 token。

### P1：Flet 桌面端

修改 `src/ui/desktop.py`、`src/ui/settings_center.py`、`src/ui/model_settings.py`、`src/ui/project_studio.py`、`src/ui/evidence_reviews.py`、`src/ui/evidence_brief.py` 和相关控件，消除高频硬编码字号、颜色、圆角和按钮尺寸。将 Markdown 与科研详情区域收敛到 ZCode 信息密度；把外观设置改为主题、界面字号、代码显示、密度和动效分组。

### P2：TUI 交互补齐

修改 `tui/src/opentui/app-shell.js`、侧栏组件、设置面板和主题同步逻辑，补齐窄屏 overlay drawer、键盘焦点、显示设置和 reduced-motion 行为。保持已有命令、审批、研究时间线、子智能体和证据视图的功能契约不变。

## 验收标准

- Flet 与 TUI 的主题模式、界面字号、代码字号、密度和动效设置能互相读取同一份配置。
- 12–20px 界面字号变化不会改变布局几何，代码和终端字号保持独立。
- 桌面端所有主要界面不再依赖裸白字、散落硬编码颜色和任意圆角。
- Flet 与 TUI 在明亮、暗色、系统主题下的文本和状态对比度通过测试。
- TUI 窄屏能打开 overlay sidebar，关闭后不丢失当前输入、审批或研究上下文。
- 关闭动效后不再播放 shimmer、过渡或装饰性帧动画。
- 运行 Python UI 测试、TUI 全量测试和新增 token/settings 回归测试。

## 不在本轮范围

- 不改科研引擎、证据链、权限语义和模型调用协议。
- 不复制或引入 ZCode 的上游业务代码。
- 不继续维护路演 PPT 生成器和演示素材。
