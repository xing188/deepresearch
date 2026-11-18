# Ubuntu 服务器部署

本文介绍 Ubuntu 24.04 LTS 的部署流程。请按自己的云主机信息配置；不要把公网 IP、SSH 私钥、API 密钥或用户数据提交到仓库。

第一步是在腾讯云控制台“登录”终端中添加本次部署的专用 SSH 公钥。对应私钥只保存在本电脑 `.data/deploy/cloud_ed25519`，不打包、不上传到服务器，也不写入聊天。

代码发布包由 `deploy/package.py` 生成，包含当前应用、网页、依赖清单和上游开源源码及许可证，不包含本地 `.env`、研究数据库、上传资料、虚拟环境、部署私钥或日志。已购买服务器不代表项目已经部署。

连接授权完成后，先确认服务器身份、系统、内存和磁盘，以及模型与搜索服务的连通性，再上传代码。安装脚本会安装 Python 环境和中文字体，并创建 `deepresearch` 系统账户、安装目录及 systemd 自启动服务。

默认服务只监听服务器的 `127.0.0.1:8000`，用于通过 SSH 隧道验证。现有 API 配置和历史数据的迁移通过 SSH 单独完成；迁移前需检查本地任务状态并备份两个 SQLite 数据库。

云服务器配置文件放在 `/opt/deepresearch/.data/settings.env`，由 systemd 的 `RESEARCH_CONFIG_FILE` 环境变量指定，可正常在网页设置中保存。本地 Windows 版本继续使用原来的根目录 `.env`。

手机公网访问另需配置网站登录、HTTPS 和域名或适当的私有访问方案。中国内地网站的备案要求以腾讯云及相关规定为准。准备阶段不直接向公网开放应用的 8000 端口。

安装示例：

```bash
tar -xzf deepresearch-release.tar.gz deploy
sudo bash deploy/install-ubuntu.sh "$PWD/deepresearch-release.tar.gz"
```

日常状态检查：

```bash
sudo systemctl status deepresearch
sudo journalctl -u deepresearch -n 80 --no-pager
```

## 私有手机访问：Tailscale

如需在手机上访问，可通过 Tailscale Serve 向 tailnet 提供 HTTPS。服务器与手机需登录同一个 Tailscale 账户；不要启用 Funnel，也不要开放公网 8000 端口。手机连接 Tailscale 后才可访问，网络连通性需在目标设备上实际验证。

服务器安装 Tailscale 客户端并执行 `tailscale up`，再由管理员完成登录授权。客户端安装说明见 [Tailscale 官方下载页](https://tailscale.com/download)。

本地 `server.py` 已增加 `--tailscale-host` 适配。现有云端版本尚不包含此改动。
更新前先检查 `/api/health` 的 `running`，有研究任务则等待结束；备份旧 `server.py` 后更新。
`deploy/package-private.py` 生成增量包 `deepresearch-private-access.tar.gz`，只包含应用入口、配置脚本和更新脚本。
将其上传 `/root`，解压到独立的 `/root/deepresearch-private-update`，执行其中的 `deploy/apply-private-update.sh`。
更新脚本检查登录和空闲状态，保留旧入口与覆盖配置备份，并在更新或后端验证失败时恢复旧版本。
更新后运行 `bash /opt/deepresearch/deploy/configure-tailscale.sh`，它从已经登录的 Tailscale 获取精确的 DNS 名称、验证后创建服务覆盖配置。
增量更新脚本会自动执行上述配置，无需再次手动运行。
应用依然只监听 loopback；只额外允许这个确切的 ts.net 主机名，且该主机名的请求只能来自 loopback。
HTTPS 的 Origin 与配置的主机名严格匹配，不信任 X-Forwarded-* 来放行外部请求。

最后执行 `tailscale serve --bg http://127.0.0.1:8000`。如提示启用 HTTPS，用户需自行打开显示的控制台地址批准。
只有完成 Serve 后，才使用它输出的 `https://…ts.net/` 地址。
检查主页、保存设置、测试连接和研究计划创建，确认不是只有 GET 请求可用。
云端为独立配置/数据库，需要在私有网页重新填入模型和搜索密钥，或通过受保护的连接单独迁移本地配置/历史；不把密钥打入代码包。

本地自动化测试在项目根目录运行 `python -m unittest discover -s tests -v`；测试使用 Mock 服务，不验证云主机上的真实网络、Tailscale 登录状态或 HTTPS 配置。部署后应按本文步骤逐项验证访问、登录和模型连接。
