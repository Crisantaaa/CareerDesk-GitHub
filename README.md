# CareerDesk 求职工作台

CareerDesk 是一个在 Windows 电脑本机运行的个人求职管理工具。它用 Streamlit 提供网页界面，用 SQLite 保存公司、岗位、申请阶段、时间节点、资料索引和备份。浏览器访问本机地址 `http://127.0.0.1:8501`，默认不提供公网服务。

## 能做什么

- 建立公司与岗位资料，使用双表 Excel 模板批量导入，并在导入前核对疑似重复项。
- 记录投递时间、申请状态、测评与面试阶段的时间节点、链接、要求和备注。
- 在总览中查看即将截止的岗位与阶段安排。
- 索引本机简历和其他文件，不复制原件；按需预览或打开。
- 导出业务数据，并创建、验证和恢复本机数据库备份。
- 可选地调用智谱 API 分析选定的岗位资料。只有主动输入自己的 API Key 并点击发送才会发起请求；这项功能需要联网，费用和可用额度以服务商账户为准。

## 在 Windows 上运行

已测试的环境是 Windows 和 Python 3.14。先安装 Python，在本仓库目录打开 PowerShell，依次运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start_careerdesk.cmd
```

也可以在安装依赖后双击 `start_careerdesk.cmd`。首次运行会在本机自动创建空的 `data/` 目录和数据库。若工作台已经运行，再次启动只会打开现有页面。`requirements-lock.txt` 记录了原开发环境的精确版本，优先使用 `requirements.txt` 安装。

## 数据与素材

公开仓库**不包含**真实数据库、备份、导出文件、简历、个人资料、API Key、虚拟环境或浏览器缓存。个人数据默认保存在本机的 `data/` 目录；上传修改时也不要把这些文件加入 Git。

本机版本使用的《星露谷物语》建筑、角色和地面图片不随仓库发布。代码在图片缺失时仍可运行，顶部场景会少一些装饰。`assets/careerdesk_gong.ico` 是工作台桌面快捷方式使用的自制“工”字图标。

GitHub 仓库用于展示和分享源码，不会自动把这个本机应用变成可供他人访问的在线网站。这个仓库是从本机工作目录整理出的一份副本；今后修改本机版本时，需要把准备公开的代码同步到此仓库后再提交。

## 项目结构

| 路径 | 用途 |
| --- | --- |
| `app.py` | Streamlit 页面入口 |
| `careerdesk/` | 数据库、业务规则、导入、备份、解析和界面辅助代码 |
| `tests/` | 使用隔离临时数据库运行的测试 |
| `.streamlit/config.toml` | 本机服务与主题配置 |
| `start_careerdesk.cmd` | Windows 启动脚本 |
| `launch_careerdesk_silent.vbs` | 给桌面快捷方式使用的无命令窗口启动器 |

运行测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

本仓库目前未附加开源许可证。公开展示代码并不自动授予他人复制或修改代码的许可；正式开放复用前需选择并添加许可证。
