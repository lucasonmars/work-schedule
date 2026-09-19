# 超简PM

按周协作的项目进度看板：组织隔离、帐号登录、格子在线填写，并自动汇总本周工作。

线上演示：[schedule.peoplepark.com.cn](https://schedule.peoplepark.com.cn)

## 启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

默认只监听本机 `http://127.0.0.1:5000`。需要对外监听时再设置环境变量：

```bash
export CHAOJIAN_HOST=0.0.0.0
export CHAOJIAN_PORT=5000
```

首次打开页面后自行注册组织帐号。数据库、会话密钥写在本地 `data/`（已 gitignore），不会进入仓库。

可选超管帐号（不设则不创建）：

```bash
export CHAOJIAN_SUPERADMIN_USER=your_admin
export CHAOJIAN_SUPERADMIN_PASSWORD=your_password
```

## 功能

- 帐号密码登录 / 注册（新建组织或申请加入）
- 纵轴：任务分类 + 明细；横轴：周
- 点击格子填写进展，保存后记下编辑人和时间
- 色点：未填 / 进行中 / 已完成 / 受阻
- 可新增分类、新增任务
- 「周工作汇总」与可选销售回款字段
- 组织管理员可发 Agent API Key（完整 Key 只在生成时显示一次）
