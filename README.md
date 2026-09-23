# 米哈游三游 · 每日自动化（原神 / 崩铁 / 绝区零）

用**社区成熟工具当引擎**，加上一层我们自己的"胶水"：定时调度、屏幕模式自动切换、配置面板、
一键下载/启动三个工具。你日常只需要双击 `panel.bat` 打开面板改配置，其余到点自动跑完、自动关游戏。

## 下载

- 最新发布包（zip，含全部调度层代码）：[v1.0.0](https://github.com/guyidong/hoyo-daily-3in1/releases/tag/v1.0.0)
- 三个引擎不在仓库里，由面板从**各自官方 Releases** 下载（BetterGI 固定 0.64.0）

## 这个项目做了什么（以及没做什么）

**做的：**
- 一份 YAML 配置驱动三款游戏的每日流程（每日任务 + 清体力/电量 + 领奖励 + 关游戏）。
- Windows 计划任务每天定时无人值守执行：零点击、零 UAC（任务以最高权限注册，子进程直接继承权限）。
- 执行前后**自动切换屏幕模式**（游戏窗口化 1920×1080 → 跑完恢复你自己的全屏设置），跑完自动关闭游戏。
- 集成面板：一键下载/更新三个上游工具（可多选、支持代理与断点续传）、分别启动它们的图形界面、
  改每日时间、查看运行日志。

**没做的（重要）：**
- **不含任何上游工具的代码或二进制**。本仓库只有我们自己的胶水代码；三个引擎由面板从各自官方 Releases 下载。
- 面板只覆盖**常用字段**（开关、要打的本、次数、队伍名、时间）。上游工具的高级设置请直接用它们自己的界面改，
  改完本项目的适配器会保留你的设置（只覆写它自己负责的键）。

## 三个引擎（上游项目 · 版权归原作者，遵循其各自许可证）

| 游戏 | 引擎 | 上游仓库 | 本项目的启动方式 |
|---|---|---|---|
| 原神 | BetterGI | https://github.com/babalae/better-genshin-impact | `tools/BetterGI/BetterGI/BetterGI.exe` |
| 崩铁 | March7thAssistant | https://github.com/moesnow/March7thAssistant | `tools/March7thAssistant/March7th Launcher.exe` |
| 绝区零 | OneDragon（ZenlessZoneZero-OneDragon） | https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon | 面板按钮 / `one_dragon_launcher.bat` |

模型的来源同样取自各上游官方仓库。本项目只按官方发布物原样下载，不修改其内容。

## 快速开始

1. 装 Python 3.11+，在项目根目录建虚拟环境并安装依赖：

   ```bat
   py -3 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. 双击 `panel.bat` 打开集成面板（浏览器自动打开 http://127.0.0.1:8900，端口被占用会自动换）。
3. 面板里勾选要用的引擎 → **下载/更新**（未装过的机器第一次用；支持填写代理）。
4. 面板里点每个引擎的 **启动界面**，用它们自己的界面完成**首次登录/初始化**（登录、分辨率、队伍等一次即可）。
5. 回到面板，改好三款游戏的配置 → **保存**；设定每日时间 → **保存定时**（这一步写系统计划任务，需要管理员权限运行一次面板）。
6. 之后就不用管了：每天到点自动依次跑完三款游戏，自动关闭游戏，日志在 `logs/daily.log`。

## 配置（`config/daily_config.yaml`）

- `scheduler`：运行顺序、总超时。
- `games.<游戏>.enabled`：是否参与每日流程。
- `games.<游戏>.targets`：要刷的本/秘境，每项可单独 `enabled`、`runs`（`0` 表示清空体力）。
- `games.<游戏>.display.auto_switch_window`：跑之前自动切成窗口化 1920×1080，跑完恢复你原来的设置。
- 首次运行会自动从 `config/daily_config.example.yaml` 生成一份默认配置。

示例（原神）:

```yaml
games:
  genshin:
    enabled: true
    party_name: "你的队伍名"   # 打秘境用的队伍
    targets:
      - { name: "山风的荆冕", runs: 0, enabled: true }
```

## 定时任务（无人值守的关键）

面板的"保存定时"会用 PowerShell 注册一个名为 `HoYoDaily` 的计划任务：

- 触发器：每天 `HH:MM`；`StartWhenAvailable=True`（开机晚了/错过了也会补跑）。
- 主体：`RunLevel=Highest` + `LogonType=Interactive`——**子进程继承最高权限**，所以引擎自己再启动游戏时不会再弹 UAC。
- 动作：`pythonw.exe auto_daily.py`（无窗口，不会突然弹黑框）。
- 互斥：`MultipleInstances=IgnoreNew`；程序内部还有单实例锁，避免重复启动引擎。

> 注意：如果你自己手动（非计划任务）运行 `auto_daily.py`，因为拿不到最高权限，启动引擎时可能弹一次 UAC —— 这是正常的。

## 屏幕模式自动切换（含"自愈"）

游戏按窗口客户区大小渲染，识别类引擎在 16:9 窗口化下最稳，所以每款游戏开跑前会自动切成 1920×1080 窗口化，
跑完再恢复成你自己的设置（例如 2560×1600 全屏）。这里有两个**必须遵守**的细节，否则会出现"第二天自己开游戏变窗口"的怪事：

1. **先杀游戏、等它彻底退出，再写回设置**。Unity 游戏在退出瞬间会把自己的显示设置回写注册表，
   先恢复再杀进程的话，那次恢复会被游戏覆盖掉。
2. **用户设置要单独存档**（`config/display_user.json`）。只靠"跑之前快照 → 跑完恢复"不够：
   一旦某次快照本身已经是残留的窗口化，之后就会把这个窗口化一路传下去，永久污染。
   所以只有当快照看起来不是自动化状态（1920×1080 窗口化）时才更新档案；恢复时若发现当前状态是残留，就改用档案。

3. **兜底（`win_guard.py`）**：万一客户端就是不认注册表，引擎跑起来后它会**整场盯着**游戏窗口，
   只要发现客户区不是 16:9（比如 2560×1600 全屏 16:10）就拉成 1920×1080，后面再变还会再拉回来；
   三个引擎都挂了这道保险。
   注意：光靠它是**不够**的（见下面绝区零那条）—— 绝区零的窗口受保护，实测连拉 10 次都拉不动。

### 绝区零特有：换"显示档"（2026-09-13 实测结论）

绝区零把显示模式存在**它自己的一份存档**里（`ZenlessZoneZero_Data\Persistent\LocalStorage\GENERAL_DATA.bin`），
启动约 8 秒后用它覆盖注册表。只改注册表没用：游戏先按注册表以 1920×1080 窗口起来，
随即自己切回 2560×1600 全屏（16:10），OneDragon 在 16:10 下识别全废（邮件/签到/体力刷本全失败），
而它的窗口受保护、外部改不动大小 —— 这是三款游戏里唯一同时占这两条的。

解决办法是**换档**：把"用户自己的显示档"和"自动化用的窗口档"分别存成快照，跑之前换窗口档、跑完换回用户档。

```bat
rem 让游戏自己生成窗口档：游戏内改成"窗口 1920x1080"后执行（此时游戏必须是关的）
mkdir config\zzz_display_profiles\windowed
copy "D:\ZenlessZoneZero Game\ZenlessZoneZero_Data\Persistent\LocalStorage\*.bin" config\zzz_display_profiles\windowed\
.venv\Scripts\python.exe display_mode.py zzz show-user > config\zzz_display_profiles\registry_windowed.json
```

跑的时候适配器会自动：`windowed` 档（3 个存档文件 + 注册表）→ 跑完 `fullscreen` 档。
这两份档含个人存档数据，**已在 `.gitignore` 里排除，不会进仓库**。

被污染了也不怕，一条命令修回来（游戏正开着会等它退出后再改）：

```bat
.venv\Scripts\python.exe display_mode.py zzz fix-user 2560 1600 --wait
.venv\Scripts\python.exe display_mode.py starrail show-user
```

## 环境变量（可选，用于自定义路径）

| 变量 | 作用 |
|---|---|
| `HOYO_BETTERGI_DIR` | BetterGI 安装目录 |
| `HOYO_MARCH7TH_DIR` | March7thAssistant 安装目录 |
| `HOYO_ONEDRAGON_DIR` | OneDragon 目录 |
| `HOYO_PROXY` | 下载/更新走代理时使用，如 `http://127.0.0.1:7890` |

## 踩坑记录（血泪，帮你少走弯路）

1. **绝区零的 OneDragon 必须用前台模式**（`background_mode: false`）。后台模式点击登录页会失败；前台模式才稳。
2. **窗口受保护**：游戏窗口对非管理员进程的 `PostMessage`/`taskkill` 会被拒绝（错误 5），所以引擎必须在管理员上下文里跑——这就是计划任务要用最高权限的原因。
3. **不要用后台模式 + 虚拟手柄去点登录页**，那是错的方向：前台 + 正常鼠标模拟就够了。
4. **屏幕模式**：游戏按窗口客户区大小渲染，跑之前统一切成 1920×1080 窗口化，跑完恢复原设置，能显著减少识别失败。
5. **配置文件写入**：不要用 PowerShell `Set-Content -Encoding utf8` 写 YAML（会带 BOM，ruamel/PyYAML 解析报错），用 Python 以 UTF-8 无 BOM 写。
6. **路径**：在生成的配置文件/脚本里统一用正斜杠 `/`，避免反斜杠被各层转义吃掉（`D:daily...` 这类事故）。
7. **实时日志**：引擎日志可能很大，判断"跑完没跑完"时只读文件尾部（例如最后 300KB），不要整文件读入。
8. **屏幕设置的两个坑**（见上一节）：恢复必须放在"游戏进程完全退出"之后；用户设置要独立存档，否则一次窗口化残留会永久传染。
10. **绝区零的显示模式存在它自己的存档里**（`LocalStorage\GENERAL_DATA.bin`），启动 8 秒后覆盖注册表；
    它的窗口还受保护、拉不动。只能"换档"（见上），改注册表/SetWindowPos 都白费。
11. **OneDragon 进程退出 ≠ 成功**：它识别失败后会自己中止（实测退出码 `0xC000013A`），
    所以 `wait()` 必须看退出码，否则会把失败报成成功。
12. **BetterGI 不要无脑跟最新版**：0.65.0 是上游重构版（发布说明自己写着"版本BUG较多"），
    实测自动秘境里「识别出战角色失败 → 卡墙角 → 后续任务全被挡」，2026-09-19/20 连挂两天。
    `tools_manager.py` 已把 BetterGI **固定到 0.64.0**（`"tag": "0.64.0"`），自动更新器也禁用了
    （`BetterGI.update.exe` → `.disabled`）。要升级先手动验证一条龙能跑完再改。
13. **BetterGI 内部失败不反映在退出码上**：进程照样正常退出，只看退出码会一直显示 `ok=True`。
    所以跑完必须扫日志特征（`游戏窗口分辨率不是 16:9` / `识别出战角色失败` / `任务启动失败` / `执行异常`），
    命中就在主日志里报 WARNING —— 已在 `adapters/bettergi.py` 里实现。
14. **日志目录是包根的 `log/`**（不是 `User/log`）。判定完成标记/失败特征时只认"本次运行之后新增的内容"
    （启动时记日志基线），否则会把上一次运行的结果误当成这次的。
9. **BetterGI 一条龙的任务开关是 GUID 键**（`TaskDefinitions` 是 `{GUID: 任务名}`）。按中文名写开关会被它自己已保存的状态整体覆盖，等于没配 —— 必须先反查 GUID 再写。

## 风险与免责

- 使用第三方工具模拟操作**违背游戏用户协议，存在封号风险**，请自行评估；本项目不对此负责。
- 运行时游戏窗口需保持前台、屏幕常亮、不要手动操作电脑；不要开画面滤镜（HDR/显卡滤镜），亮度保持默认。
- 本项目为个人自用工具，按 MIT 许可证发布，不提供任何担保。三款游戏的名称、素材、商标归米哈游所有。

## 许可证

MIT（见 LICENSE）。上游三个引擎各自遵循其仓库中的许可证，版权归其作者所有。
