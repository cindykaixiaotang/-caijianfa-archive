#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财政部行政处罚决定书爬虫 v3
解决JS动态写入问题：使用正则表达式直接匹配源码
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import requests
import json
import re
import time
import os

LIST_URL = "https://www.mof.gov.cn/gp/xxgkml/jdjcj/index.htm"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

OUTPUT_FILE = "mof_penalty_data.json"


def fetch_page(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.encoding = 'utf-8'
        return resp.text
    except Exception as e:
        print(f"[X] 获取页面失败: {e}")
        return None


def parse_list_page(html):
    """从HTML源码中用正则提取链接"""
    # 使用更宽松的正则
    hrefs = re.findall(r'<a\s+href="([^"]+)"', html)
    titles = re.findall(r'var\s+str\s*=\s*"([^"]+)"', html)
    
    # 取较小值确保配对
    min_len = min(len(hrefs), len(titles))
    
    links = []
    for i in range(min_len):
        href = hrefs[i]
        title = titles[i]
        
        # 只处理处罚决定书
        if '处罚决定书' not in title:
            continue
        
        # 清理标题
        title = title.strip()
        
        # 构建完整URL
        full_url = "https://www.mof.gov.cn" + href.replace('./', '/gp/xxgkml/jdjcj/')
        
        links.append({"title": title, "href": full_url})
    
    print(f"[OK] 找到 {len(links)} 条处罚决定书")
    return links


def parse_detail_page(html, url):
    """从详情页提取信息"""
    result = {
        "title": "",
        "href": url,
        "pubDate": "",
        "companies": "",
        "penalty": "",
        "issues": ""
    }
    
    # 1. 提取标题 - 通常在 <h1> 或类似元素中
    h1_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
    if h1_match:
        result["title"] = h1_match.group(1).strip()
    
    # 2. 提取日期
    date_match = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日)', html)
    if date_match:
        result["pubDate"] = date_match.group(1)
    
    # 3. 获取正文内容
    # sqbxzContent 等容器因嵌套 div 贪婪匹配会截断，直接从 body 整体提取更可靠
    body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)
    if body_match:
        content = re.sub(r'<[^>]+>', ' ', body_match.group(1))
        content = re.sub(r'\s+', ' ', content).strip()
    else:
        content = re.sub(r'<[^>]+>', ' ', html)
        content = re.sub(r'\s+', ' ', content).strip()
    
    # 清理HTML实体
    import html
    content = html.unescape(content)  # &nbsp; -> 空格, &amp; -> & 等
    content = re.sub(r'&[a-zA-Z]+;', '', content)  # 清除其他HTML实体
    # 清理各种空白字符：普通空格、不间断空格\u00a0、制表符等
    content = re.sub(r'[\u00a0\t\r]+', ' ', content)  # 特殊空格转普通空格
    content = re.sub(r' {2,}', ' ', content)  # 多个空格合并为一个
    content = content.strip()
    
    # 4. 提取涉及公司
    # 优先匹配公司名称：公司名+地址
    # 先找"当事人：XXX 地址："的模式
    match = re.search(r'当\s*事\s*人[：:]\s*([^\n]{2,80}公司[^\n]{0,20})\s*地\s*址', content)
    if match:
        result["companies"] = match.group(1).strip()
    
    # 如果上面没匹配到，匹配到"地址"之前的公司名
    if not result["companies"]:
        match = re.search(r'当\s*事\s*人[：:]\s*([^\n]{2,80})\s*地\s*址', content)
        if match:
            result["companies"] = match.group(1).strip()
    
    # 如果还没匹配到，匹配到第一个标点之前的
    if not result["companies"]:
        match = re.search(r'当\s*事\s*人[：:]\s*([^，。\n]{2,60})', content)
        if match:
            result["companies"] = match.group(1).strip()
    
    # 模式2: 从标题提取，如"财政部行政处罚决定书（XXX）」"
    if not result["companies"] and result["title"]:
        match = re.search(r'（([^）]+)）', result["title"])
        if match:
            candidate = match.group(1)
            # 检查是否像公司名
            if '有限' in candidate or '评估' in candidate or '会计' in candidate or '集团' in candidate:
                result["companies"] = candidate
    
    # 5. 提取处罚措施
    penalty_parts = []
    
    # 警告
    if '警告' in content:
        penalty_parts.append('警告')
    
    # 没收 XXX万元 - 匹配"没收...违法所得XXX万元"或"没收XXX万元"
    没收_matches = re.findall(r'没收[^违法]*违法所得(\d+(?:\.\d+)?)万元', content)
    if not 没收_matches:
        没收_matches = re.findall(r'没收(\d+(?:\.\d+)?)万元', content)
    for m in 没收_matches:
        penalty_parts.append(f'没收{m}万元')
    
    # 罚款 XXX万元 - 匹配"罚款XXX万元"或"并处罚款XXX万元"
    罚款_matches = re.findall(r'(?:并)?处罚款(\d+(?:\.\d+)?)万元', content)
    for m in 罚款_matches:
        penalty_parts.append(f'罚款{m}万元')
    
    # 责令停业
    停业_match = re.search(r'责令停业(\d+个月|整顿)', content)
    if 停业_match:
        penalty_parts.append(f'责令停业{停业_match.group(1)}')
    
    # 吊销
    吊销_match = re.search(r'吊销(\w+)', content)
    if 吊销_match:
        penalty_parts.append(f'吊销{吊销_match.group(1)}')
    
    if penalty_parts:
        result["penalty"] = '，'.join(penalty_parts[:6])
    
    # 6. 提取检查发现的问题 - 两种页面结构：
    #    A. "如下：  一、XXX  二、XXX  上述事实"（公司案件，多条问题）
    #    B. "如下：  2024年... 上述事实"（个人案件，直接是内容，无"一、"标题）
    issues_match = None
    
    # 优先：结构A - "如下：" 后接 "一、XXX"
    issues_match = re.search(
        r'(?:检查发现的主要问题[^。\n]*如下[：:]\s*|检查发现的主要问题[：:\s]+)'
        r'(一[、.、]\s*[\s\S]*?)(?=上述事实|当事人对本机关|本机关认为|依据|$)',
        content
    )
    
    if not issues_match:
        # 结构B - "如下：" 后直接接正文内容（无"一、"）
        issues_match = re.search(
            r'检查发现的主要问题[^。\n]*如下[：:]\s*(.{20,}?)(?=上述事实|当事人对本机关|本机关认为)',
            content,
            re.DOTALL
        )
    
    if not issues_match:
        # 备用1：直接找 "一、XXX" 到 "上述事实"
        issues_match = re.search(
            r'(一[、.、]\s*.{5,}[\s\S]*?)(?=上述事实|当事人对本机关|本机关认为)',
            content
        )
    
    if not issues_match:
        # 备用2：找 "存在以下问题" 后面的内容
        issues_match = re.search(
            r'存在以下问题[：:]\s*([\s\S]{50,2000}?)(?:上述事实|当事人|$)',
            content
        )
    
    if issues_match:
        raw_issues = issues_match.group(1).strip()
        raw_issues = re.sub(r'\n{3,}', '\n\n', raw_issues)
        raw_issues = raw_issues.strip()
        result["issues"] = raw_issues
    
    return result


def crawl_penalties(max_count=20):
    print("=" * 60)
    print("财政部行政处罚决定书爬虫 v3 (正则提取版)")
    print("=" * 60)
    
    print(f"\n[INFO] 获取列表页...")
    html = fetch_page(LIST_URL)
    if not html:
        print("[X] 获取列表页失败")
        return []
    
    links = parse_list_page(html)
    if not links:
        print("[X] 未找到任何链接")
        return []
    
    results = []
    print(f"\n[INFO] 解析详情页...")
    
    for i, link in enumerate(links[:max_count]):
        print(f"  [{i+1}/{min(max_count, len(links))}] {link['title'][:30]}...", end=" ")
        
        detail_html = fetch_page(link["href"])
        if not detail_html:
            print("[X]")
            continue
        
        info = parse_detail_page(detail_html, link["href"])
        
        if not info["title"]:
            info["title"] = link["title"]
        
        companies = info["companies"] or "（未识别）"
        penalty = info["penalty"] or "（未识别）"
        print(f"[OK] {companies[:15]} | {penalty[:20]}")
        
        results.append(info)
        time.sleep(0.5)
    
    return results


def generate_data(entries):
    if not entries:
        return None
    
    # 只保留最新日期的记录
    latest_date = entries[0].get("pubDate", "")
    filtered_entries = [e for e in entries if e.get("pubDate") == latest_date]
    
    return {
        "ref": filtered_entries[0] if filtered_entries else entries[0],
        "dateA": latest_date,
        "filtered": filtered_entries,
        "cachedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "totalCount": len(filtered_entries)
    }


def save_output(data):
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] 数据已保存到: {OUTPUT_FILE}")


def merge_to_html():
    if not os.path.exists(OUTPUT_FILE):
        return
    
    with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    
    html_file = "mof_latest.html"
    if os.path.exists(html_file):
        with open(html_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        new_content = re.sub(
            r'const EMBEDDED_DATA = \{.*?\};',
            f'const EMBEDDED_DATA = {json_str};',
            content,
            flags=re.DOTALL
        )
        
        if new_content != content:
            with open(html_file + ".bak", 'w', encoding='utf-8') as f:
                f.write(content)
            with open(html_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"[OK] 已更新 {html_file}")


def main():
    results = crawl_penalties(max_count=20)
    
    if results:
        data = generate_data(results)
        save_output(data)
        
        print("\n" + "=" * 60)
        print(f"总记录: {len(results)}")
        print(f"最新: {results[0].get('pubDate', '?')}")
        print(f"最早: {results[-1].get('pubDate', '?')}")
        
        merge_to_html()
    else:
        print("[X] 未获取到数据")


if __name__ == "__main__":
    main()
