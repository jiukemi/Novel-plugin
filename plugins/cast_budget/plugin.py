# -*- coding: utf-8 -*-
"""
角色戏份预算：统计出场占比与断档/过曝预警。

扫描正文（优先）+ 细纲文本中的角色名提及，产出：
- 戏份占比 / 出场章数
- 核心角色失踪、临时角色过曝、弧光角色长期断档等预警
报告落盘：projects/{id}/plugins/cast_budget/
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from plugins.base import PluginMeta, PluginRunResult, plugin_report_dir
from skills.pure.character_store import cast_tier_label


def _load_characters(novel_root: Path) -> List[Dict[str, Any]]:
    try:
        from skills.pure.character_store import CharacterStore

        data = CharacterStore(novel_root).load()
        items = data.get("items") or data.get("characters") or []
        return [x for x in items if isinstance(x, dict) and (x.get("name") or "").strip()]
    except Exception:
        p = novel_root / "characters" / "characters.json"
        if not p.exists():
            return []
        raw = json.loads(p.read_text(encoding="utf-8"))
        items = raw.get("items") or raw.get("characters") or []
        return [x for x in items if isinstance(x, dict) and (x.get("name") or "").strip()]


def _chapter_texts(novel_root: Path) -> List[Tuple[int, str, str]]:
    """返回 [(number, source, text)]，source=body|detail。"""
    from skills.pure.chapter_index import iter_bodies

    out: List[Tuple[int, str, str]] = []
    # 正文 md：章号取 project.json 的卷序全局号，别从文件名反推（多卷会撞号）
    for num, text, _row in iter_bodies(novel_root, min_chars=80, strip_title=False):
        out.append((num, "body", text.strip()))
    # 细纲补扫描（尚无正文时）
    detail_path = novel_root / "details" / "detail.json"
    if detail_path.exists():
        try:
            detail = json.loads(detail_path.read_text(encoding="utf-8"))
        except Exception:
            detail = {}
        have_body = {n for n, s, _ in out if s == "body"}
        for c in detail.get("chapters") or []:
            num = int(c.get("number") or 0)
            if num <= 0 or num in have_body:
                continue
            blob = (c.get("content") or "").strip()
            fields = c.get("fields") if isinstance(c.get("fields"), dict) else {}
            if fields:
                blob = blob + "\n" + json.dumps(fields, ensure_ascii=False)
            if len(re.sub(r"\s+", "", blob)) >= 40:
                out.append((num, "detail", blob))
    # 仅有大纲时：用分卷大纲作弱信号（章号用卷 start 作代表章）
    if not out:
        outline_path = novel_root / "outlines" / "outline.json"
        if outline_path.exists():
            try:
                ol = json.loads(outline_path.read_text(encoding="utf-8"))
            except Exception:
                ol = {}
            for v in ol.get("volumes") or []:
                text = (v.get("outline") or "").strip()
                if len(re.sub(r"\s+", "", text)) < 40:
                    continue
                start = int(v.get("start") or 0) or 1
                out.append((start, "outline", text))
    out.sort(key=lambda x: x[0])
    return out


def _count_mentions(text: str, name: str) -> int:
    name = (name or "").strip()
    if len(name) < 2:
        return 0
    return len(re.findall(re.escape(name), text))


class CastBudgetPlugin:
    meta = PluginMeta(
        id="cast_budget",
        name="角色戏份预算",
        version="0.1.0",
        category="cast",
        description="统计角色出场/提及，预警戏份失衡、核心失踪、临时过曝",
        premium=False,
        agent_tools=["cast_budget.run"],
    )

    def available(self) -> bool:
        return True

    def run(self, novel_root: Path, **kwargs: Any) -> PluginRunResult:
        root = Path(novel_root)
        chars = _load_characters(root)
        chapters = _chapter_texts(root)
        if not chars:
            return PluginRunResult(
                ok=False,
                plugin_id=self.meta.id,
                summary="暂无角色卡，请先在角色页添加或提取",
            )
        if not chapters:
            return PluginRunResult(
                ok=False,
                plugin_id=self.meta.id,
                summary="暂无可扫描文本（正文或细纲为空）",
            )

        # 长名优先，减少短名误伤（仍可能误计，属启发式）
        names = sorted(
            [(str(c.get("name") or "").strip(), c) for c in chars],
            key=lambda x: len(x[0]),
            reverse=True,
        )
        per_char: Dict[str, Dict[str, Any]] = {}
        for name, c in names:
            per_char[name] = {
                "id": c.get("id") or "",
                "name": name,
                "cast_tier": (c.get("cast_tier") or "temp").strip() or "temp",
                "role": c.get("role") or "",
                "mentions": 0,
                "chapters": [],
                "sources": {"body": 0, "detail": 0, "outline": 0},
            }

        scanned = 0
        for num, source, text in chapters:
            scanned += 1
            for name, _c in names:
                cnt = _count_mentions(text, name)
                if cnt <= 0:
                    continue
                row = per_char[name]
                row["mentions"] += cnt
                row["sources"][source] = int(row["sources"].get(source) or 0) + cnt
                if num not in row["chapters"]:
                    row["chapters"].append(num)

        total_mentions = sum(r["mentions"] for r in per_char.values()) or 1
        chapter_nums = [n for n, _, _ in chapters]
        span = max(chapter_nums) - min(chapter_nums) + 1 if chapter_nums else 0
        sources_used = {s for _, s, _ in chapters}
        # 仅大纲弱信号时不做断档预警（卷代表章会虚高 gap）
        gap_alerts_ok = bool(sources_used & {"body", "detail"})

        rows = []
        alerts: List[Dict[str, Any]] = []
        for name, row in per_char.items():
            share = round(100.0 * row["mentions"] / total_mentions, 1)
            chs = sorted(row["chapters"])
            gap = 0
            if chs and span > 0 and gap_alerts_ok:
                # 最大连续空白章距
                prev = min(chapter_nums) - 1
                for x in chs + [max(chapter_nums) + 1]:
                    gap = max(gap, x - prev - 1)
                    prev = x
            item = {
                **row,
                "chapters": chs,
                "chapter_count": len(chs),
                "share_pct": share,
                "max_gap": gap,
            }
            rows.append(item)
            tier = row["cast_tier"]
            if tier == "core" and row["mentions"] == 0 and scanned >= 3:
                alerts.append(
                    {
                        "severity": "high",
                        "code": "core_missing",
                        "name": name,
                        "message": f"核心角色「{name}」在已扫描文本中零提及",
                    }
                )
            elif gap_alerts_ok and tier == "core" and gap >= 15:
                alerts.append(
                    {
                        "severity": "medium",
                        "code": "core_gap",
                        "name": name,
                        "message": f"核心角色「{name}」最长断档约 {gap} 章",
                    }
                )
            elif gap_alerts_ok and tier == "arc" and gap >= 40 and row["mentions"] > 0:
                alerts.append(
                    {
                        "severity": "medium",
                        "code": "arc_gap",
                        "name": name,
                        "message": f"弧光角色「{name}」最长断档约 {gap} 章，注意回收",
                    }
                )
            elif tier == "temp" and share >= 12 and row["chapter_count"] >= 8:
                alerts.append(
                    {
                        "severity": "medium",
                        "code": "temp_overexposed",
                        "name": name,
                        "message": f"临时角色「{name}」戏份偏高（{share}% / {row['chapter_count']}章），考虑升弧光或收敛",
                    }
                )
            elif tier == "core" and share < 3 and scanned >= 10 and row["mentions"] > 0:
                alerts.append(
                    {
                        "severity": "low",
                        "code": "core_underused",
                        "name": name,
                        "message": f"核心角色「{name}」戏份偏低（{share}%）",
                    }
                )

        rows.sort(key=lambda x: (-x["mentions"], x["name"]))
        alerts.sort(key=lambda a: {"high": 0, "medium": 1, "low": 2}.get(a["severity"], 9))

        report = {
            "plugin": self.meta.id,
            "version": self.meta.version,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "scanned_chapters": scanned,
            "chapter_range": [min(chapter_nums), max(chapter_nums)] if chapter_nums else [],
            "character_count": len(rows),
            "total_mentions": total_mentions,
            "source_note": "正文优先；无正文用细纲；再无则用分卷大纲（启发式点名，非语义理解）",
            "characters": rows,
            "alerts": alerts,
            "missing_candidates": [],
            "summary": (
                f"已扫描 {scanned} 章 · {len(rows)} 角色 · "
                f"预警 {len(alerts)} 条（高 {sum(1 for a in alerts if a['severity']=='high')}）"
            ),
        }
        # 附带花名册差分（进料），供提案 create_character
        try:
            from skills.pure.roster_diff import scan_roster_diff

            diff = scan_roster_diff(root, limit=12)
            report["missing_candidates"] = diff.get("candidates") or []
            miss_n = len(report["missing_candidates"])
            if miss_n:
                report["summary"] += f" · 花名册缺卡候选 {miss_n}"
                for c in report["missing_candidates"][:8]:
                    alerts.append(
                        {
                            "severity": "medium",
                            "code": "roster_missing",
                            "name": c.get("name"),
                            "message": (
                                f"「{c.get('name')}」在大纲/细纲点名但花名册无卡，"
                                f"建议入库为「{cast_tier_label(c.get('cast_tier') or 'temp')}」"
                            ),
                            "cast_tier": c.get("cast_tier") or "temp",
                            "camp": c.get("camp") or "中立",
                            "role": c.get("role") or "配角",
                            "debut_chapter": c.get("debut_chapter") or 0,
                        }
                    )
                report["alerts"] = alerts
        except Exception:
            pass

        out_dir = plugin_report_dir(root, self.meta.id)
        latest = out_dir / "latest.json"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        hist = out_dir / f"report-{stamp}.json"
        text = json.dumps(report, ensure_ascii=False, indent=2)
        latest.write_text(text, encoding="utf-8")
        hist.write_text(text, encoding="utf-8")
        md = out_dir / "latest.md"
        md.write_text(_to_markdown(report), encoding="utf-8")

        return PluginRunResult(
            ok=True,
            plugin_id=self.meta.id,
            summary=report["summary"],
            report_path=str(latest.relative_to(root)) if root in latest.parents else str(latest),
            data=report,
            alerts=alerts,
        )


def _to_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# 角色戏份预算报告",
        "",
        f"- 生成时间：{report.get('generated_at')}",
        f"- {report.get('summary')}",
        f"- 说明：{report.get('source_note')}",
        "",
        "## 预警",
        "",
    ]
    alerts = report.get("alerts") or []
    if not alerts:
        lines.append("无预警。")
    else:
        for a in alerts:
            lines.append(f"- **[{a.get('severity')}]** {a.get('message')}")
    lines += ["", "## 戏份排行", "", "| 角色 | 层级 | 提及 | 出场章 | 占比 | 最大断档 |", "|---|---|---:|---:|---:|---:|"]
    for r in report.get("characters") or []:
        lines.append(
            f"| {r.get('name')} | {r.get('cast_tier')} | {r.get('mentions')} | "
            f"{r.get('chapter_count')} | {r.get('share_pct')}% | {r.get('max_gap')} |"
        )
    lines.append("")
    return "\n".join(lines)
