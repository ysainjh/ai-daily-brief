import os
import smtplib
import feedparser
from email.mime.text import MIMEText
from jinja2 import Template

DEFAULT_IMAGE = "https://images.unsplash.com/photo-1677442136019-21780ecad995?auto=format&fit=crop&w=1200&q=60"

RSS_FEEDS = [
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://hnrss.org/frontpage",
    "https://techcrunch.com/feed/"
]

def score(title):
    keywords = ["AI","GPT","OpenAI","Google","Meta","Apple","security","漏洞"]
    s = 5
    for k in keywords:
        if k.lower() in title.lower():
            s += 1
    return min(s,10)

def fetch_news():
    items = []
    for url in RSS_FEEDS:
        feed = feedparser.parse(url)
        for e in feed.entries[:5]:
            items.append({
                "title": e.get("title",""),
                "link": e.get("link",""),
                "summary": (e.get("summary","") or "")[:200],
                "image": DEFAULT_IMAGE,
                "score": score(e.get("title",""))
            })
    items.sort(key=lambda x: x["score"], reverse=True)
    return items

def render(items):
    with open("templates/mail.html","r",encoding="utf-8") as f:
        template = Template(f.read())

    top5 = items[:5]

    return template.render(
        title="AI 科技晨报 V1.1",
        date="今日",
        weekday="一二三四五六日",
        sections=[{"name":"全部新闻","items":items}],
        top5=top5
    )

def send(html):
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT","465"))
    user = os.getenv("SMTP_USER")
    pwd = os.getenv("SMTP_PASSWORD")
    to = os.getenv("MAIL_TO")

    msg = MIMEText(html,"html","utf-8")
    msg["Subject"] = "AI 科技晨报 V1.1"
    msg["From"] = user
    msg["To"] = to

    s = smtplib.SMTP_SSL(host, port)
    s.login(user, pwd)
    s.sendmail(user, [to], msg.as_string())
    s.quit()

def main():
    items = fetch_news()
    html = render(items)
    send(html)

if __name__ == "__main__":
    main()
