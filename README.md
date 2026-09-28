# 草榴技术讨论区 RSS（自建）

草榴官方的 RSS 接口已经下线，这个仓库用 GitHub Actions 每 30 分钟抓取一次
技术讨论区（技術討論區，fid=7）的帖子列表，生成带首帖全文的标准 RSS，通过 GitHub Pages 对外提供。

## 搭建步骤（3 步，约 2 分钟）

1. 在 GitHub 新建一个**公开**仓库（Public，私有仓库的 Pages 要收费），名字随意，比如 `t66y-rss`。
   注意：仓库必须是公开的，否则 Pages 无法免费启用。
2. 把本目录的文件传到仓库：
   - `fetch.py`（放到根目录）
   - `content_cache.json`（放到根目录，帖子正文缓存，首次全量种子）
   - `feed.xml`（放到根目录，首次生成的全文 feed）
   - `.github/workflows/update-feed.yml`（保持这个路径）
   
   网页端操作：仓库首页 → Add file → Upload files，把两个文件拖进去即可。
   传完后到 Actions 标签页手动点一次 `更新 RSS` → Run workflow，确认能跑通。
3. 开 Pages：仓库 Settings → Pages → Build and deployment 选 **Deploy from a branch**，
   Branch 选 `main`、目录选 `/ (root)` → Save。等 1～2 分钟，
   访问 `https://<你的GitHub用户名>.github.io/<仓库名>/feed.xml`，能看到 XML 就成功了。

## 订阅

把上面的 `feed.xml` 地址加进 Inoreader / Reeder 即可，Inoreader 会自动轮询更新。

## 说明

- 每轮抓取：1 个列表页 + 最多 30 个新帖正文页（带 1 秒间隔），对源站压力小。
- feed 内容为帖子标题 + **首帖全文**（HTML，含图片）+ 作者 + 最后回复时间 + 原帖链接。
- 正文缓存在 `content_cache.json` 里随仓库提交，新帖自动增量抓取。
- 如果哪天 `t66y.com` 域名换了，改 `fetch.py` 里的 `BASE` 即可。
