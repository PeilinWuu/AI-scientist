# Ubuntu 22.04 LTS 兼容性审计

审计范围：当前仓库运行时代码、FastAPI/Streamlit 入口、Competition 1B runtime、文档解析器、Pillow 图像输出、受控 Python sandbox、依赖锁定文件。未修改 benchmark、Qwen evidence 或科研逻辑。

## 结论

**BLOCKER：无。** 代码层面未发现会阻止 Ubuntu 22.04 部署的 Windows 专属依赖。正式上线仍需要按 `UBUNTU_DEPLOY_REQUIREMENTS.md` 完成 systemd、Nginx/TLS、权限、备份和监控配置；这些属于 **MANUAL** 运维事项。

## 检查结果

| 类别 | 状态 | 结论 |
|---|---|---|
| 路径与文件系统 | PASS | 运行时代码使用 `pathlib.Path`、环境变量和项目相对目录；未发现 `C:\`、`D:\`、`Users\Administrator` 硬编码。上传、项目、artifact、job 持久化均按目录创建并使用原子替换。 |
| 反斜杠/`os.path` | PASS | 未发现运行时反斜杠拼接或依赖 Windows 分隔符；少量 `os.path` 搜索结果不在生产运行路径。 |
| Windows 专属 API | PASS | `src/`、`app_streamlit.py`、`tools/` 未使用 PowerShell、cmd、where、taskkill、win32、pywin32、COM 或注册表。README/REPRODUCE 中的 PowerShell 仅为 Windows 文档示例。 |
| subprocess | PASS | readiness 使用参数列表调用 `pytest`，无 shell；sandbox 使用 `sys.executable -I`、PIPE、显式 UTF-8、超时和 psutil 进程树清理；Linux 不依赖 Windows CREATE_* 标志。 |
| sandbox Linux 行为 | PASS | Linux 下 `Popen`、`psutil.Process.children/kill/wait_procs`、超时和网络环境清空均有对应实现。内存监控为进程 RSS 轮询，属于应用级限制。 |
| 文件权限/临时目录 | MANUAL | Ubuntu 服务账户必须对 `AI_SCIENTIST_PROJECTS_DIR`、`RUNS_DIR`、`AI_SCIENTIST_COMPETITION_DIR` 具有读写权限；建议独立账户、`umask 027`、独立数据盘和备份。 |
| 编码 | PASS | 生产文本读写均显式 `encoding="utf-8"`；上传文本解析按 UTF-8/UTF-8-SIG/GB18030 回退；未依赖 GBK 系统 locale。 |
| 依赖 | PASS | lock 中无 Windows-only package；FastAPI、Uvicorn、Streamlit、NumPy、Pandas、Pillow、pypdf、openpyxl、xlrd、defusedxml、psutil 均有 Linux 使用路径。建议 Ubuntu 使用 x86_64 与锁定 Python 版本。 |
| 系统级 apt 依赖 | MANUAL | 需安装 Python、venv、编译工具和 Nginx；详见安装清单。常规 x86_64 pip wheels 不需要 Microsoft Office。 |
| PDF/Excel/CSV/XML | PASS | 使用 pypdf、openpyxl、xlrd、标准库/defusedxml 和 Pandas；未调用 Office、COM 或 Excel 进程。 |
| Matplotlib/DISPLAY | PASS | 当前图像产物由 Pillow `Image`/`ImageDraw` 生成，不创建 GUI figure，不需要 DISPLAY 或 X server。 |
| 中文字体 | MANUAL | Pillow 默认字体可能无法显示中文；若部署截图或中文图像文本，安装 Noto CJK 并在应用层显式指定字体。核心数值流程不依赖字体。 |
| FastAPI/Uvicorn | PASS | `python -m uvicorn src.main_api:app --host 127.0.0.1 --port 8000 --proxy-headers` 可由 systemd 运行；Nginx 负责公网入口。 |
| Streamlit | PASS | `streamlit run app_streamlit.py --server.headless true --server.address 127.0.0.1 --server.port 8501` 可无桌面运行；Nginx 示例已保留 websocket headers。 |
| 环境变量 | PASS | `DASHSCOPE_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL`、搜索/预算/目录配置均通过环境变量读取；不依赖 PowerShell。 |
| 平台判断 | PASS | 未发现 `sys.platform`、`os.name`、`platform.system()` 分支；执行审计只记录 `sys.platform` 字段。 |
| 长期运行 3 个月 | MANUAL | 需要 systemd `Restart=always`、日志轮转、磁盘监控、HTTPS、备份、健康检查和 WAF/IP 限流；应用本身不替代这些运维措施。 |

## 最小修复记录

- 前序公网部署清理已将 Competition readiness 的 `output_root` 从服务器绝对路径改为 `competition/1b/api_demo`，本次复核确认 Ubuntu 不会暴露部署路径。
- 已确认上传接口 20 MiB 限制与 Nginx 25 MiB 请求体上限相容。
- 未改变 API contract、科研状态机、benchmark 数值或 Qwen evidence。

## 审计扫描说明

仓库中可能出现 Windows 路径、PowerShell 和 `platform: win32` 的地方主要位于历史运行产物、测试快照或 Windows 复现文档，不会在 Ubuntu 服务启动时执行。提交公网包时仍应按 manifest 排除临时日志、缓存和本机产物。
