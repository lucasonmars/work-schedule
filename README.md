# 超简PM

按周填格子的项目进度看板。组织隔离、帐号登录、格子在线改，本周工作自动汇总。够用就好，不堆功能。

线上可直接试用：[schedule.peoplepark.com.cn](https://schedule.peoplepark.com.cn)

<p>
  <img src="docs/login.png" alt="超简PM 登录页" width="800" />
</p>
<p>
  <img src="static/promo-board.svg" alt="进度看板示意" width="400" />
  <img src="static/promo-sales.svg" alt="销售回款示意" width="400" />
</p>

## 它解决什么

小团队周报经常散落在聊天和表格里。超简PM 就一张表：

- 纵轴是任务分类和明细，横轴是自然周
- 点格子填写「工作任务 / 完成内容 / 状态」
- 黄点进行中、绿点完成、红点受阻
- 「周工作汇总」把本周格子收成一篇给领导看的东西
- 可选销售管理：预期、确认、回款，一张图看完

每个组织的数据隔离。注册时可以建新组织（你当管理员），或申请加入已有组织（等审批）。

## 30 秒上手

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

浏览器打开 [http://127.0.0.1:5000](http://127.0.0.1:5000)，自己注册一个组织即可。

默认只绑本机。若要局域网访问：

```bash
export CHAOJIAN_HOST=0.0.0.0
export CHAOJIAN_PORT=5000
python app.py
```

数据、会话密钥都在本地 `data/`（已写入 `.gitignore`），不会进 Git。

## 功能

- 帐号密码登录 / 注册（新建组织或申请加入）
- 进度看板：分类、任务、按周格子、@ 同事
- 周工作汇总、个人本周记录
- 可选销售回款表与累计曲线
- 组织管理员：改组织名、加人、审批加入申请
- Agent API：先把提示词发给 Agent，等它要 Key 时再生成（完整 Key 只出现一次）

## 可选超管

不配环境变量就不会创建超管帐号。自托管若需要平台后台：

```bash
export CHAOJIAN_SUPERADMIN_USER=your_admin
export CHAOJIAN_SUPERADMIN_PASSWORD=your_password
```

不要把真实密码写进仓库或提交到 Git。

## 技术

Python 3 + Flask + SQLite。单文件 `app.py`，静态资源在 `static/`，页面在 `templates/`。

## 许可

源码按仓库现状公开，可自用、可改、可二次部署。上线前请自己保管好 `data/` 和超管环境变量。
