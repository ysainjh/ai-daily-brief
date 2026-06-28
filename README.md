# 开发者 AI · 科技 · 世界新闻晨报（基础版）

每天 7:30 自动抓取新闻，生成中文 HTML 晨报，并通过 163 邮箱 SMTP 发送到指定邮箱。

## 功能

- GitHub Actions 每天自动运行
- AI / 大模型、科技新闻、世界新闻、开发者资讯
- HTML 邮件模板
- 每条新闻包含标题、摘要、为什么值得关注、原文链接、配图
- 支持 OpenAI 兼容 API 总结；不配置 API 时使用 RSS 原摘要兜底

## 部署步骤

### 1. 创建 GitHub 仓库

新建一个仓库，例如：`daily-news`，把本项目所有文件上传进去。

### 2. 开启 163 邮箱 SMTP

163 邮箱路径通常为：设置 → POP3/SMTP/IMAP → 开启 SMTP → 获取授权码。

注意：`SMTP_PASSWORD` 填授权码，不是邮箱登录密码。

### 3. 配置 GitHub Secrets

进入仓库：Settings → Secrets and variables → Actions → New repository secret。

必填：

| Secret 名称 | 示例 |
| --- | --- |
| SMTP_HOST | smtp.163.com |
| SMTP_PORT | 465 |
| SMTP_USER | yourname@163.com |
| SMTP_PASSWORD | 163 邮箱 SMTP 授权码 |
| MAIL_TO | yourname@163.com |
| MAIL_FROM | yourname@163.com |

可选（用于 AI 中文总结）：

| Secret 名称 | 示例 |
| --- | --- |
| OPENAI_API_KEY | sk-xxx |
| OPENAI_BASE_URL | https://api.openai.com/v1 |
| OPENAI_MODEL | gpt-4o-mini |

如果你用兼容 OpenAI 的模型服务，把 `OPENAI_BASE_URL` 改成对应地址即可。

### 4. 手动测试

进入 GitHub 仓库：Actions → Daily News Email → Run workflow。

如果成功，你会收到一封测试晨报。

### 5. 定时发送

默认配置为：

```yaml
cron: "30 23 * * *"
```

这是 **北京时间每天 7:30**。

如果你想改为日本时间 7:30，应改为：

```yaml
cron: "30 22 * * *"
```

## 调整栏目

修改 `config.yaml` 即可增加或删除新闻源。

## 常见问题

### 没收到邮件

1. 检查 GitHub Actions 是否运行成功。
2. 检查 Secrets 是否填写正确。
3. 检查 163 邮箱是否开启 SMTP。
4. 检查邮箱垃圾箱。
5. SMTP_PASSWORD 必须是授权码，不是登录密码。

### GitHub Actions 时间不准

GitHub Actions 的 schedule 使用 UTC 时间，且可能有几分钟延迟。需要精确触发可以改用服务器或云函数。
