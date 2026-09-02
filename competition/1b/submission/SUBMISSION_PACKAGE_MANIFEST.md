# Competition 1B 提交包清单

## 必须包含

- 最终 PDF/PPT、演示截图和视频链接/说明
- `source/`：干净源码与锁定依赖
- `competition/1b/cases/`：代表性旗舰案例
- `competition/1b/results/`：脱敏 evidence、benchmark summary 和 coverage
- `README.md`、`REPRODUCE.md`
- `competition/1b/submission/API_TEST.md`
- `competition/1b/submission/DEPLOY_ECS.md` 与 `deployment/nginx.conf.example`
- demo guide、案例 README 和必要的审计产物

## 明确排除

- `.env`、任何 API Key、token、Authorization header 或完整 secret
- 临时项目、临时运行目录、临时日志和本机调试输出
- `__pycache__/`、`.pytest_cache/`、`.coverage`、虚拟环境和编辑器目录
- 含个人绝对路径、机器用户名或公网凭证的文件
- 未经脱敏的模型原始响应或请求日志

打包前执行 `git status --short`、`git diff --check`、`python -m pytest -q`，并用 `rg` 检查 secret 与个人路径。提交包只从已提交 Git 工作树导出，不直接压缩整个开发目录。
