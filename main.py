import os
import re
import ssl
import smtplib
import hashlib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from zoneinfo import ZoneInfo
from typing import Dict, List

import yaml
import feedparser
import requests
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape


def env_required(name: str) -> str:
    value = os.getenv(name, '').strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def clean_html(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def item_id(title: str, link: str) -> str:
    return hashlib.md5(f"{title}|{link}".encode("utf-8")).hexdigest()


def extract_image(entry: Dict) -> str:
    # RSS media fields vary a lot. Try common fields first.
    media = entry.get("media_content") or entry.get("media_thumbnail") or []
    if media and isinstance(media, list):
        url = media[0].get("url")
        if url:
            return url
    links = entry.get("links") or []
    for link in links:
        if str(link.get("type", "")).startswith("image/") and link.get("href"):
            return link["href"]
    # Fallback image so email layout remains stable.
    return "https://via.placeholder.com/960x540.png?text=Daily+Briefing"


def fetch_feed(url: str, section: str) -> List[Dict]:
    parsed = feedparser.parse(url)
    results = []
    for entry in parsed.entries[:10]:
        title = clean_html(entry.get("title", ""))
        link = entry.get("link", "")
        summary = clean_html(entry.get("summary", ""))
        published = entry.get("published", "") or entry.get("updated", "")
        if title and link:
            results.append({
                "id": item_id(title, link),
                "section": section,
                "title": title,
                "link": link,
                "summary_raw": summary,
                "published": published,
                "image": extract_image(entry),
            })
    return results


def dedupe(items: List[Dict]) -> List[Dict]:
    seen = set()
    out = []
    for item in items:
        key = item["id"]
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def llm_summarize(items: List[Dict]) -> List[Dict]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        for item in items:
            raw = item.get("summary_raw") or item["title"]
            item["summary"] = raw[:180] + ("..." if len(raw) > 180 else "")
            item["why"] = "这条内容与今日 AI、科技、开发或国际局势相关，建议快速了解。"
        return items

    prompt_items = "\n".join([
        f"[{i+1}] 标题：{x['title']}\n摘要：{x.get('summary_raw','')}\n链接：{x['link']}"
        for i, x in enumerate(items)
    ])
    prompt = f"""
你是中文新闻晨报编辑。请根据下面新闻条目，为每条生成：
1）不超过80字的中文摘要；
2）不超过40字的“为什么值得关注”。
要求：客观、简洁，不编造标题外的信息。输出严格 JSON 数组，每项包含 summary 和 why。

新闻：
{prompt_items}
""".strip()

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是严谨的中文新闻编辑。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        import json
        content = re.sub(r"^```json|```$", "", content.strip(), flags=re.I | re.M).strip()
        data = json.loads(content)
        for item, result in zip(items, data):
            item["summary"] = result.get("summary") or item.get("summary_raw") or item["title"]
            item["why"] = result.get("why") or "值得快速了解。"
    except Exception as e:
        for item in items:
            item["summary"] = (item.get("summary_raw") or item["title"])[:180]
            item["why"] = f"AI 总结失败，已使用 RSS 摘要。错误：{type(e).__name__}"
    return items


def build_sections(config: Dict) -> List[Dict]:
    sections = []
    for sec in config["sections"]:
        raw_items = []
        for feed in sec["feeds"]:
            raw_items.extend(fetch_feed(feed, sec["name"]))
        items = dedupe(raw_items)[: sec.get("max_items", 5)]
        items = llm_summarize(items)
        sections.append({"name": sec["name"], "items": items})
    return sections


def render_html(config: Dict, sections: List[Dict]) -> str:
    tz = ZoneInfo(config["app"].get("timezone", "Asia/Shanghai"))
    now = datetime.now(tz)
    env = Environment(
        loader=FileSystemLoader("templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("mail.html")
    return template.render(
        title=config["app"]["title"],
        date=now.strftime("%Y-%m-%d"),
        weekday="一二三四五六日"[now.weekday()],
        sections=sections,
    )


def send_email(config: Dict, html: str):
    smtp_host = os.getenv("SMTP_HOST", "smtp.163.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = env_required("SMTP_USER")
    smtp_password = env_required("SMTP_PASSWORD")
    mail_to = env_required("MAIL_TO")
    mail_from = os.getenv("MAIL_FROM", smtp_user)

    tz = ZoneInfo(config["app"].get("timezone", "Asia/Shanghai"))
    today = datetime.now(tz).strftime("%Y-%m-%d")
    subject = f"{config['email'].get('subject_prefix', '每日晨报')} · {today}"

    msg = MIMEMultipart("alternative")
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText("请使用支持 HTML 的邮件客户端查看晨报。", "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
        server.login(smtp_user, smtp_password)
        server.sendmail(mail_from, [x.strip() for x in mail_to.split(",")], msg.as_string())


def main():
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    sections = build_sections(config)
    html = render_html(config, sections)
    with open("latest.html", "w", encoding="utf-8") as f:
        f.write(html)
    send_email(config, html)
    print("Daily briefing email sent.")


if __name__ == "__main__":
    main()
