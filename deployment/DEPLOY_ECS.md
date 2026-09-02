# ECS 公网部署说明

架构：公网 80/443 → Nginx；`/` 反向代理 Streamlit `127.0.0.1:8501`，`/api/` 反向代理 FastAPI `127.0.0.1:8000`。HTTPS 证书建议由 ECS 前置负载均衡或 Certbot 管理。

## 服务器环境

1. 安装 Python 3.13、Nginx，并创建独立虚拟环境。
2. 上传比赛源码，执行 `pip install -r requirements.lock.txt`。
3. 仅通过服务器环境变量或 systemd `EnvironmentFile` 注入 `DASHSCOPE_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL` 等配置；不要上传 `.env`。
4. 复制 `nginx.conf.example` 到 Nginx 配置目录，替换域名并启用站点。

## 启动命令

```bash
python -m uvicorn src.main_api:app --host 127.0.0.1 --port 8000 --proxy-headers
streamlit run app_streamlit.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

Streamlit websocket 所需的 `Upgrade`/`Connection` 头和 300 秒读写超时已在示例配置中设置。防火墙只开放 80/443，8000/8501 仅监听本机。

## 上线检查

```bash
curl https://YOUR_DOMAIN/health
curl https://YOUR_DOMAIN/api/competition/1b/readiness
```

确认 `/docs` 可访问、前端能加载、上传超过 20 MiB 被拒绝，并为 Nginx/WAF 配置身份认证、IP 速率限制、请求体限制和日志脱敏。项目默认模型调用预算为每项目 50 次；不要在公网开放调试路由或匿名模型测试接口。

## 回滚与数据

应用代码与 `data/research_projects` 分开备份；回滚只切换代码版本，不删除项目证据。定期备份 `competition/1b` 代表性案例和审计产物。
