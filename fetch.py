#!/usr/bin/env python3
"""抓取草榴社区技术讨论区（達蓋爾的旗幟，fid=16），生成带首帖全文的 RSS 2.0。

流程：
  1. 抓板块列表页，得到最新帖子（标题/链接/作者/最后回复时间）
  2. 对没抓过正文的帖子，抓帖子页提取首帖全文，存入 content_cache.json
     （每轮最多新增 FETCH_CAP 个，礼貌间隔；首次全量用本地跑一次做种子）
  3. 用缓存生成 feed.xml，每条 item 的 description 即首帖全文 HTML

用法: python3 fetch.py [工作目录，默认当前目录]
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from html.parser import HTMLParser
from xml.sax.saxutils import escape

BASE = "https://www.t66y.com"
SECTION_URL = f"{BASE}/thread0806.php?fid=7"
FEED_TITLE = "草榴社区 - 技術討論區"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TZ8 = timezone(timedelta(hours=8))
FETCH_CAP = 30       # 每轮最多新增抓取正文的帖子数
FETCH_DELAY = 1.0    # 抓取间隔（秒），对源站礼貌一点


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", errors="replace")
    return raw.replace('\\"', '"').replace("\\'", "'")


def parse_list(data: str):
    threads = []
    rows = re.findall(r'<tr class="tr3 t_one tac">(.*?)</tr>', data, re.S)
    for row in rows:
        m = re.search(r'<h3><a href="(/htm_data/[^"]+\.html)"[^>]*>(.*?)</a></h3>', row, re.S)
        if not m:
            continue
        href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not title:
            continue
        link = BASE + href
        author = ""
        am = re.search(r'class="bl">([^<>]{1,30})</a>', row)
        if am:
            author = am.group(1).strip()
        pub_ts = None
        lm = re.search(r'read\.php\?tid=\d+[^"\']*["\'][^>]*data-timestamp="(\d+)"', row)
        if lm:
            pub_ts = int(lm.group(1))
        else:
            om = re.search(r'data-timestamp="(\d+)s?"', row)
            if om:
                pub_ts = int(om.group(1))
        pub_date = format_datetime(datetime.fromtimestamp(pub_ts, TZ8)) if pub_ts else ""
        threads.append({"title": title, "link": link, "author": author,
                        "pubDate": pub_date, "ts": pub_ts or 0})
    seen, uniq = set(), []
    for t in threads:
        if t["link"] not in seen:
            seen.add(t["link"])
            uniq.append(t)
    return uniq


class FirstPostExtractor(HTMLParser):
    """提取第一个 div.tpc_content（首帖正文）的完整 HTML。"""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.in_target = False
        self.depth = 0
        self.parts = []
        self.done = False

    def _emit(self, s):
        if self.in_target and not self.done:
            self.parts.append(s)

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        d = dict(attrs)
        if not self.in_target:
            cls = d.get("class", "")
            if tag == "div" and ("tpc_content" in cls or d.get("id") == "conttpc"):
                self.in_target = True
                self.depth = 1
                self.parts.append(self.get_starttag_text())
            return
        self._emit(self.get_starttag_text())
        if tag == "div":
            self.depth += 1

    def handle_endtag(self, tag):
        if self.done or not self.in_target:
            return
        self._emit(f"</{tag}>")
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.in_target = False
                self.done = True

    def handle_startendtag(self, tag, attrs):
        if self.done:
            return
        if self.in_target:
            self._emit(self.get_starttag_text())

    def handle_data(self, data):
        self._emit(data)

    def handle_entityref(self, name):
        self._emit(f"&{name};")

    def handle_charref(self, name):
        self._emit(f"&#{name};")

    def handle_comment(self, data):
        self._emit(f"<!--{data}-->")

    def html(self):
        return "".join(self.parts)


def decode_redir_links(html: str) -> str:
    """把草榴的跳转链接 https://2023.redircdn.com/?<真实地址>&z 还原为直链。

    真实地址里 '.' 被混淆成 '______'（与跳转页自身的 var url 一致）。
    还原后点链接直达图片/视频，不用再过 JS 倒计时中转页。
    """
    def repl(m):
        inner = m.group(1)
        if inner.endswith("&z"):
            inner = inner[:-2]
        return 'href="' + inner.replace("______", ".") + '"'
    return re.sub(r'href="https://2023\.redircdn\.com/\?([^"]+)"', repl, html)


def embed_23img(html: str) -> str:
    """23img 的 viewer 页链接改直链并内嵌显示。

    <a href="https://23img.com/l/?i=/i/2026/09/28/ftrvkm.jpg">...</a>
      -> <a href="https://23img.com/i/2026/09/28/ftrvkm.jpg"><img ...></a>
    直链已验证返回 image/jpeg。
    """
    def repl(m):
        direct = "https://23img.com" + m.group(1)
        return (f'<a target="_blank" href="{direct}">'
                f'<img src="{direct}" referrerpolicy="no-referrer" '
                f'style="max-width:100%;height:auto;"></a>')
    return re.sub(r'<a[^>]*href="https://23img\.com/l/\?i=([^"]+)"[^>]*>.*?</a>',
                  repl, html, flags=re.S)


def fix_lazy_imgs(html: str) -> str:
    """懒加载图片还原：<img iyl-data=... ess-data='直链' ...> -> 正常 <img src='直链'>。

    草榴帖子页的图片是懒加载占位（src 为广告检测图或缺失），真实地址在 ess-data 里。
    """
    def repl(m):
        url = m.group(1)
        return (f'<img src="{url}" referrerpolicy="no-referrer" '
                f'style="max-width:100%;height:auto;" loading="lazy">')
    return re.sub(r"""<img\b[^>]*?\bess-data\s*=\s*['"]([^'"]+)['"][^>]*>""",
                  repl, html)


def extract_first_post(page_html: str) -> str:
    ex = FirstPostExtractor()
    ex.feed(page_html)
    html = ex.html().strip()
    if not html:
        return ""
    # 相对路径转绝对
    html = re.sub(r'(src|href)="/(?!/)', rf'\1="{BASE}/', html)
    # 协议相对地址补 https
    html = re.sub(r'(src|href)="//', r'\1="https://', html)
    # 跳转链接还原为直链
    html = decode_redir_links(html)
    # 23img viewer 页改直链并内嵌显示
    html = embed_23img(html)
    # 懒加载图片占位还原为真实图片
    html = fix_lazy_imgs(html)
    # 去掉点赞按钮等 UI 残留
    html = re.sub(r'<div[^>]*class="t_like"[^>]*>.*?</div>', "", html, flags=re.S)
    return html.strip()


def load_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def build_rss(threads, cache) -> str:
    now = format_datetime(datetime.now(TZ8))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        f"<title>{escape(FEED_TITLE)}</title>",
        f"<link>{escape(SECTION_URL)}</link>",
        f"<description>{escape(FEED_TITLE)} 最新帖子全文（自建抓取，每 30 分钟更新）</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        "<language>zh-cn</language>",
    ]
    for t in threads:
        entry = cache.get(t["link"], {})
        content = entry.get("content", "")
        if content:
            desc = content
        else:
            fb = [f"作者：{t['author']}" if t["author"] else "",
                  f"原帖：{t['link']}"]
            desc = "\n".join(x for x in fb if x)
        desc = desc.replace("]]>", "]]&gt;")
        parts.append("<item>")
        parts.append(f"<title>{escape(t['title'])}</title>")
        parts.append(f"<link>{escape(t['link'])}</link>")
        parts.append(f"<guid isPermaLink=\"true\">{escape(t['link'])}</guid>")
        if t["pubDate"]:
            parts.append(f"<pubDate>{t['pubDate']}</pubDate>")
        if t["author"]:
            parts.append(f"<author>{escape(t['author'])}</author>")
        parts.append(f"<description><![CDATA[{desc}]]></description>")
        parts.append("</item>")
    parts += ["</channel>", "</rss>", ""]
    return "\n".join(parts)


def main():
    workdir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(workdir, exist_ok=True)
    cache_path = os.path.join(workdir, "content_cache.json")
    feed_path = os.path.join(workdir, "feed.xml")

    threads = parse_list(http_get(SECTION_URL))
    if not threads:
        raise SystemExit("列表页解析到 0 个帖子，放弃写入以免清空旧 feed")

    cache = load_cache(cache_path)
    new_links = [t["link"] for t in threads if t["link"] not in cache][:FETCH_CAP]
    fetched = 0
    for i, link in enumerate(new_links):
        try:
            content = extract_first_post(http_get(link))
            t = next(x for x in threads if x["link"] == link)
            if content:
                cache[link] = {"title": t["title"], "author": t["author"],
                               "pubDate": t["pubDate"], "content": content}
                fetched += 1
            else:
                print(f"正文为空，跳过: {link}")
        except Exception as e:
            print(f"抓取失败 {link}: {e}")
        if i < len(new_links) - 1:
            time.sleep(FETCH_DELAY)

    # 只保留当前列表里的帖子，控制缓存体积
    keep = {t["link"] for t in threads}
    cache = {k: v for k, v in cache.items() if k in keep}

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    with open(feed_path, "w", encoding="utf-8") as f:
        f.write(build_rss(threads, cache))
    with_full = sum(1 for t in threads if cache.get(t["link"], {}).get("content"))
    print(f"OK: {len(threads)} threads, 新增正文 {fetched}, "
          f"全文覆盖 {with_full}/{len(threads)} -> {feed_path}")


if __name__ == "__main__":
    main()
