import html
import json
import os
import re
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml
from jinja2 import Template


CONFIG_PATH = "config.yaml"
DEFAULT_IMAGE = "https://images.unsplash.com/photo-1504711434969-e33886168f5c?auto=format&fit=crop&w=1200&q=60"
WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
IMPORTANT_KEYWORDS = [
    "中国", "China", "美国", "U.S.", "US", "欧盟", "EU", "俄罗斯", "Russia",
    "乌克兰", "Ukraine", "中东", "Middle East", "G7", "G20", "联合国", "UN",
    "央行", "central bank", "利率", "inflation", "election", "选举", "war",
    "conflict", "tariff", "关税", "能源", "semiconductor", "芯片", "AI",
    "economy", "经济", "trade", "贸易", "diplomacy", "外交", "政策",
]
DOMESTIC_KEYWORDS = [
    "中国", "国内", "北京", "上海", "广东", "国务院", "财政部", "商务部",
    "央行", "人民币", "A股", "高考", "民生", "政策", "中纪委", "中央",
    "香港", "澳门", "台湾", "新疆", "西藏", "China", "Chinese",
]
GLOBAL_ONLY_KEYWORDS = [
    "美国", "特朗普", "欧盟", "俄罗斯", "乌克兰", "伊朗", "以色列",
    "巴勒斯坦", "北约", "NATO", "EU", "Russia", "Ukraine", "Iran",
]
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ai-daily-brief/1.0; +https://github.com/ysainjh/ai-daily-brief)"
}
DEFAULT_EDITORIAL_PROMPT = (
    "你是给中国读者写每日要闻简报的中文新闻编辑。"
    "请把新闻标题、摘要和关注理由改写成自然、克制、准确的简体中文。"
    "人名、机构名、公司名、产品名、模型名、政策名、股票代码等专有名词不要硬翻译；"
    "已有通行中文译名的国家、城市、国际组织可以使用中文。"
    "不要夸张，不要编造 RSS 没有提供的信息。只输出 JSON。"
)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_briefs(config):
    briefs = config.get("briefs")
    if briefs:
        return briefs

    return [{
        "key": "default",
        "title": config.get("app", {}).get("title", "每日简报"),
        "subject_prefix": config.get("email", {}).get("subject_prefix", "每日简报"),
        "to_env": "MAIL_TO",
        "max_items_total": config.get("app", {}).get("max_items_total", 12),
        "max_items_per_section": config.get("app", {}).get("max_items_per_section", 6),
        "sections": config.get("sections", []),
    }]


def strip_html(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def source_from_entry(entry, link):
    source = entry.get("source", {})
    if isinstance(source, dict) and source.get("title"):
        return source["title"]

    title = entry.get("title", "")
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()

    host = urlparse(link or "").netloc
    return host.replace("www.", "") if host else "未知来源"


def clean_title(title, source):
    title = title.strip()
    if source:
        escaped_source = re.escape(source)
        title = re.sub(
            rf"\s*(?:[-－–—]\s*[^|]{{1,50}}\s*\|\s*|\s*[-－–—|]\s*){escaped_source}$",
            "",
            title,
        ).strip()
    title = re.sub(r"\s*\|\s*[^|]{2,60}$", "", title).strip()
    title = re.sub(r"\s*[-－–—]\s*(?:新闻中心|中国日报网|[^-－–—]{2,20}(?:网|报|新闻|News))$", "", title).strip()
    return title.strip()


def extract_image(entry):
    for field in ("media_content", "media_thumbnail"):
        media_items = entry.get(field) or []
        if media_items and media_items[0].get("url"):
            return media_items[0]["url"]

    links = entry.get("links") or []
    for link in links:
        if str(link.get("type", "")).startswith("image/") and link.get("href"):
            return link["href"]

    return DEFAULT_IMAGE


def parse_feed(feed_url):
    try:
        response = requests.get(feed_url, headers=REQUEST_HEADERS, timeout=30)
        response.raise_for_status()
        return feedparser.parse(response.content)
    except Exception as exc:
        print(f"Fetch failed: {feed_url} ({exc})")
        return feedparser.parse("")


def score_item(item, section_index):
    text = f"{item['title']} {item['summary']}"
    score = 100 - section_index * 5
    for keyword in IMPORTANT_KEYWORDS:
        if keyword.lower() in text.lower():
            score += 6
    if item.get("published"):
        score += 3
    return score


def normalize_link(link):
    return (link or "").split("?utm_", 1)[0].strip()


def matches_domestic_focus(section_name, title, summary):
    if "国内" not in section_name:
        return True
    title_has_domestic = any(keyword.lower() in title.lower() for keyword in DOMESTIC_KEYWORDS)
    title_has_global = any(keyword.lower() in title.lower() for keyword in GLOBAL_ONLY_KEYWORDS)
    if title_has_global and not title_has_domestic:
        return False
    text = f"{title} {summary}"
    return any(keyword.lower() in text.lower() for keyword in DOMESTIC_KEYWORDS)


def should_skip_feed(feed_url):
    if openai_configured():
        return False
    return "hl=en" in feed_url or "ceid=US:en" in feed_url


def fetch_section(section, section_index, default_limit):
    items = []
    max_items = section.get("max_items", default_limit)

    for feed_url in section.get("feeds", []):
        if should_skip_feed(feed_url):
            continue
        feed = parse_feed(feed_url)
        for entry in feed.entries[: max_items * 3]:
            link = normalize_link(entry.get("link", ""))
            source = source_from_entry(entry, link)
            raw_title = clean_title(entry.get("title", ""), source)
            summary = strip_html(entry.get("summary", ""))[:400]

            if not raw_title or not link:
                continue
            if not matches_domestic_focus(section["name"], raw_title, summary):
                continue

            item = {
                "title": raw_title,
                "link": link,
                "summary": summary,
                "why": "",
                "source": source,
                "image": extract_image(entry),
                "section": section["name"],
                "published": entry.get("published", ""),
            }
            item["score"] = score_item(item, section_index)
            items.append(item)

    items.sort(key=lambda item: item["score"], reverse=True)
    return items[:max_items]


def dedupe_items(sections):
    seen_links = set()
    seen_titles = set()
    deduped_sections = []

    for section in sections:
        unique_items = []
        for item in section["items"]:
            title_key = re.sub(r"\W+", "", item["title"].lower())[:80]
            link_key = item["link"]
            if link_key in seen_links or title_key in seen_titles:
                continue
            seen_links.add(link_key)
            seen_titles.add(title_key)
            unique_items.append(item)

        deduped_sections.append({**section, "items": unique_items})

    return deduped_sections


def fetch_news(config, brief):
    default_limit = brief.get("max_items_per_section", config.get("app", {}).get("max_items_per_section", 6))
    sections = []

    for index, section_config in enumerate(brief.get("sections", [])):
        sections.append({
            "name": section_config["name"],
            "items": fetch_section(section_config, index, default_limit),
        })

    sections = dedupe_items(sections)
    max_total = brief.get("max_items_total", config.get("app", {}).get("max_items_total", 12))
    total = 0
    trimmed = []

    for section in sections:
        remaining = max_total - total
        if remaining <= 0:
            break
        items = section["items"][:remaining]
        total += len(items)
        trimmed.append({**section, "items": items})

    return trimmed


def openai_configured():
    return bool(os.getenv("OPENAI_API_KEY"))


def ask_ai_for_chinese(item, brief):
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    url = f"{base_url}/chat/completions"

    system_prompt = brief.get("editorial_prompt", DEFAULT_EDITORIAL_PROMPT)
    user_prompt = {
        "section": item["section"],
        "title": item["title"],
        "summary": item["summary"],
        "source": item["source"],
    }

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "请返回 JSON，字段为 title、summary、why。"
                        "summary 控制在 70 个中文字符以内，why 控制在 36 个中文字符以内。\n"
                        + json.dumps(user_prompt, ensure_ascii=False)
                    ),
                },
            ],
        },
        timeout=45,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    data = json.loads(content)

    return {
        **item,
        "title": data.get("title", item["title"]).strip() or item["title"],
        "summary": data.get("summary", item["summary"]).strip() or item["summary"],
        "why": data.get("why", "").strip(),
    }


def fallback_polish(item, brief):
    summary = item["summary"] or "暂无摘要，请阅读原文了解详情。"
    why = brief.get("fallback_why", "与全球或国内局势变化有关，值得跟进。")
    return {**item, "summary": summary[:120], "why": why}


def polish_sections(sections, brief):
    polished_sections = []
    use_ai = openai_configured()

    for section in sections:
        polished_items = []
        for item in section["items"]:
            if use_ai:
                try:
                    polished_items.append(ask_ai_for_chinese(item, brief))
                    continue
                except Exception as exc:
                    print(f"AI polish failed for {item['title']}: {exc}")
            polished_items.append(fallback_polish(item, brief))

        polished_sections.append({**section, "items": polished_items})

    return polished_sections


def flatten_items(sections):
    items = []
    for section in sections:
        items.extend(section["items"])
    return sorted(items, key=lambda item: item["score"], reverse=True)


def now_in_timezone(config):
    timezone = config.get("app", {}).get("timezone", "Asia/Shanghai")
    try:
        return datetime.now(ZoneInfo(timezone))
    except Exception:
        return datetime.now()


def render(config, brief, sections):
    with open("templates/mail.html", "r", encoding="utf-8") as f:
        template = Template(f.read())

    now = now_in_timezone(config)
    top_items = flatten_items(sections)[:5]

    return template.render(
        title=brief.get("title", config.get("app", {}).get("title", "每日简报")),
        date=now.strftime("%Y年%m月%d日"),
        weekday=WEEKDAYS[now.weekday()],
        sections=sections,
        top_items=top_items,
    )


def resolve_recipient(brief):
    if brief.get("to"):
        return brief["to"]
    return os.getenv(brief.get("to_env", "MAIL_TO"))


def send(config, brief, html_body):
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER")
    pwd = os.getenv("SMTP_PASSWORD")
    mail_from = os.getenv("MAIL_FROM", user)
    mail_to = resolve_recipient(brief)

    if not all([host, user, pwd, mail_to]):
        raise RuntimeError("SMTP_HOST、SMTP_USER、SMTP_PASSWORD、MAIL_TO 必须配置完整")

    subject_prefix = brief.get("subject_prefix", config.get("email", {}).get("subject_prefix", "每日简报"))
    subject = f"{subject_prefix} {now_in_timezone(config).strftime('%Y-%m-%d')}"

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to

    with smtplib.SMTP_SSL(host, port) as smtp:
        smtp.login(user, pwd)
        smtp.sendmail(mail_from, [mail_to], msg.as_string())


def main():
    config = load_config()
    for brief in get_briefs(config):
        sections = fetch_news(config, brief)
        sections = polish_sections(sections, brief)
        html_body = render(config, brief, sections)
        send(config, brief, html_body)


if __name__ == "__main__":
    main()
