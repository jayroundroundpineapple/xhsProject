#!/usr/bin/env python3
"""
xhs_collector.py — 小红书笔记搜索采集脚手架
============================================
用途：按关键词采集搜索结果中的笔记数据（标题/作者/点赞/收藏/评论数/链接），存为 CSV。
定位：竞品分析与选题监控。仅限个人研究使用，请保持低频采集（默认限页、限速）。

⚠️ 说明：小红书 web API 带有 x-s/x-t 签名风控，签名会不定期变化。
本脚本支持两种模式：
  1) 直接模式：浏览器登录后导出 Cookie，脚本内完成签名（签名算法失效时不可用）
  2) Playwright 模式（推荐）：用真实浏览器页面执行搜索并读取接口响应，抗风控更稳

用法：
  export XHS_COOKIE="你的cookie"
  python xhs_collector.py --keyword "桌面收纳" --pages 2 --mode direct
  python xhs_collector.py --keyword "桌面收纳" --pages 2 --mode browser

输出：data/xhs_notes_<关键词>_<日期>.csv
"""

import argparse
import csv
import os
import sys
import time
from datetime import date

try:
    import requests
except ImportError:
    sys.exit("请先安装依赖: pip install requests pandas")


SEARCH_URL = "https://edith.xiaohongshu.com/api/sns/web/v1/search/notes"


def parse_note_card(card):
    """从搜索结果卡片中提取结构化字段。"""
    note = card.get("note_card") or card.get("noteCard") or {}
    interact = note.get("interact_info") or {}
    user = note.get("user") or {}
    return {
        "标题": (note.get("display_title") or note.get("title") or "").strip(),
        "作者": user.get("nickname", ""),
        "作者ID": user.get("user_id", ""),
        "点赞": interact.get("liked_count", "0"),
        "收藏": interact.get("collected_count", "0"),
        "评论": interact.get("comment_count", "0"),
        "类型": note.get("type", ""),  # video / normal
        "笔记ID": note.get("note_id", card.get("id", "")),
        "链接": f"https://www.xiaohongshu.com/explore/{note.get('note_id', card.get('id', ''))}",
        "采集时间": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def collect_direct(keyword, pages, cookie):
    """模式1：直接请求 API。需要 cookie 且签名有效（x-s/x-t 由请求时生成，失效需更新签名方案）。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Cookie": cookie,
        "Referer": "https://www.xiaohongshu.com/",
    }
    rows = []
    for page in range(1, pages + 1):
        params = {
            "keyword": keyword,
            "page": page,
            "page_size": 20,
            "search_id": "",  # 可选：从浏览器请求中复制真实 search_id 提升成功率
            "sort": "general",     # general 综合 / popularity 热门 / time 最新
            "note_type": "0",
        }
        try:
            resp = requests.get(SEARCH_URL, headers=headers, params=params, timeout=15)
            data = resp.json()
        except Exception as e:
            print(f"[page {page}] 请求失败: {e}（多为签名/风控问题，请改用 --mode browser）")
            break
        items = (data.get("data") or {}).get("items") or []
        if not items:
            print(f"[page {page}] 无结果或被风控，停止。响应: {str(data)[:200]}")
            break
        for card in items:
            rows.append(parse_note_card(card))
        print(f"[page {page}] 获取 {len(items)} 条")
        time.sleep(3)  # 低频礼貌采集，勿调小
    return rows


def collect_browser(keyword, pages):
    """模式2：Playwright 真实浏览器。需要先 pip install playwright && playwright install chromium，
    并在首次运行时手动扫码登录小红书，登录态会持久化。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("浏览器模式需要: pip install playwright && playwright install chromium")

    rows = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_dir := os.path.expanduser("~/.xhs_session"),
            headless=False, viewport={"width": 1400, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        captured = []
        def on_response(resp):
            if "search/notes" in resp.url:
                try:
                    captured.append(resp.json())
                except Exception:
                    pass

        page.on("response", on_response)
        page.goto("https://www.xiaohongshu.com/search_result?keyword=" + keyword, wait_until="domcontentloaded")
        if "登录" in page.title():
            input("请在弹出的浏览器中完成登录，然后回车继续...")
        for i in range(pages):
            page.mouse.wheel(0, 2000)
            time.sleep(2.5)
        ctx.close()

    for data in captured:
        for card in (data.get("data") or {}).get("items") or []:
            rows.append(parse_note_card(card))
    print(f"共捕获 {len(rows)} 条笔记")
    return rows


def save_csv(rows, keyword):
    os.makedirs("data", exist_ok=True)
    path = f"data/xhs_notes_{keyword}_{date.today().isoformat()}.csv"
    if not rows:
        print("没有数据可保存。")
        return path
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"✅ 已保存 {len(rows)} 条 → {path}")
    return path


def analyze(path):
    """简单分析：按互动量排序，打印 Top10。"""
    try:
        import pandas as pd
    except ImportError:
        return
    df = pd.read_csv(path)
    for col in ["点赞", "收藏", "评论"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["互动总量"] = df["点赞"] + df["收藏"] + df["评论"]
    top = df.sort_values("互动总量", ascending=False).head(10)
    print("\n=== Top10 高互动笔记（竞品拆解目标）===")
    for _, r in top.iterrows():
        print(f"  赞{int(r['点赞']):>7} 藏{int(r['收藏']):>7} 评{int(r['评论']):>5}  {r['标题'][:38]}")


def main():
    ap = argparse.ArgumentParser(description="小红书搜索数据采集（仅限个人竞品研究）")
    ap.add_argument("--keyword", required=True, help="搜索关键词")
    ap.add_argument("--pages", type=int, default=2, help="采集页数（默认2，请勿调大）")
    ap.add_argument("--mode", choices=["direct", "browser"], default="direct",
                    help="direct=API直连(需XHS_COOKIE) / browser=Playwright(推荐)")
    ap.add_argument("--analyze", action="store_true", help="采集后输出 Top10 分析")
    args = ap.parse_args()

    if args.mode == "direct":
        cookie = os.environ.get("XHS_COOKIE", "")
        if not cookie:
            sys.exit("请设置 XHS_COOKIE 环境变量，或使用 --mode browser")
        rows = collect_direct(args.keyword, args.pages, cookie)
    else:
        rows = collect_browser(args.keyword, args.pages)

    path = save_csv(rows, args.keyword)
    if args.analyze:
        analyze(path)


if __name__ == "__main__":
    main()
