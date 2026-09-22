# -*- coding: utf-8 -*-
"""
世界观铁律断言：从设定文本提取「禁止/不得」类规则，扫描细纲与正文命中。
本地启发式，不调 LLM。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from plugins.base import PluginMeta, PluginRunResult, plugin_report_dir

# 常见后期设定词（可被设定中的禁止项覆盖/补充）
_DEFAULT_LATE_TERMS = (
    "元丹",
    "金丹",
    "化神",
    "渡劫",
    "飞升",
    "四阶",
    "五阶",
    "灵霄",
    "共鸣大成",
    "炼虚",
    "合体期",
)


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _settings_blob(novel_root: Path) -> str:
    chunks: List[str] = []
    settings_dir = novel_root / "settings"
    if settings_dir.is_dir():
        for p in sorted(settings_dir.glob("*.json")):
            try:
                chunks.append(p.read_text(encoding="utf-8")[:8000])
            except Exception:
                pass
        for p in sorted(settings_dir.glob("*.md")):
            try:
                chunks.append(p.read_text(encoding="utf-8")[:8000])
            except Exception:
                pass
    # 细纲自定义模板里常写卷纪律
    detail = _load_json(novel_root / "details" / "detail.json")
    for t in detail.get("custom_templates") or []:
        if isinstance(t, dict):
            chunks.append(str(t.get("content") or "")[:4000])
    return "\n".join(chunks)


def _extract_ban_terms(blob: str) -> List[str]:
    terms: List[str] = []
    # 「禁止X」「不得X」「勿X」
    for m in re.finditer(
        r"(?:禁止|不得|勿|严禁|不能提前写|本卷禁止)([^\n。；;，,]{1,24})",
        blob,
    ):
        frag = m.group(1).strip(" ：:、/与和及")
        # 拆顿号
        for part in re.split(r"[、/与及]", frag):
            part = part.strip()
            if 2 <= len(part) <= 16:
                terms.append(part)
    # 明示列表：禁止修炼法、共鸣、四阶
    for m in re.finditer(r"禁止([^。\n]{2,40})", blob):
        for part in re.split(r"[、，,；;]", m.group(1)):
            part = re.sub(r"^(写|出现|提及)", "", part.strip())
            if 2 <= len(part) <= 12:
                terms.append(part)
    # 去重保序
    seen: Set[str] = set()
    out: List[str] = []
    for t in terms + list(_DEFAULT_LATE_TERMS):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:40]


def _iter_scan_texts(novel_root: Path) -> List[Tuple[int, str, str]]:
    """[(chapter_number, source, text)]"""
    out: List[Tuple[int, str, str]] = []
    detail = _load_json(novel_root / "details" / "detail.json")
    for c in detail.get("chapters") or []:
        num = int(c.get("number") or 0)
        if num <= 0:
            continue
        blob = (c.get("content") or "").strip()
        fields = c.get("fields") if isinstance(c.get("fields"), dict) else {}
        if fields:
            blob = blob + "\n" + json.dumps(fields, ensure_ascii=False)
        if len(re.sub(r"\s+", "", blob)) >= 20:
            out.append((num, "detail", blob))
    from skills.pure.chapter_index import iter_bodies

    have = {n for n, s, _ in out if s == "detail"}
    # 章号按 project.json 卷序取全局号；文件名在多卷里会重复
    for num, text, _row in iter_bodies(novel_root, min_chars=80, strip_title=False):
        out.append((num, "body", text))
        have.add(num)
    out.sort(key=lambda x: (x[0], 0 if x[1] == "detail" else 1))
    return out


def _volume_early_end(novel_root: Path) -> int:
    """第一卷结束章号，用于『前期』扫描窗口。"""
    outline = _load_json(novel_root / "outlines" / "outline.json")
    vols = outline.get("volumes") or []
    if vols:
        end = int(vols[0].get("end") or 0)
        if end > 0:
            return end
    structure = _load_json(novel_root / "structure" / "structure.json")
    vols = structure.get("volumes") or []
    if vols:
        end = int(vols[0].get("end") or 0)
        if end > 0:
            return end
    return 70


class IronRulesPlugin:
    meta = PluginMeta(
        id="iron_rules",
        name="世界观铁律断言",
        version="0.1.0",
        category="ledger",
        description="从设定/卷纪律提取禁止项，扫描细纲与正文是否提前写未揭晓内容",
        premium=False,
        agent_tools=["iron_rules.run"],
    )

    def available(self) -> bool:
        return True

    def run(self, novel_root: Path, **kwargs: Any) -> PluginRunResult:
        root = Path(novel_root)
        blob = _settings_blob(root)
        bans = _extract_ban_terms(blob)
        early_end = _volume_early_end(root)
        texts = _iter_scan_texts(root)

        hits: List[Dict[str, Any]] = []
        alerts: List[Dict[str, Any]] = []

        if not bans:
            alerts.append(
                {
                    "severity": "low",
                    "code": "no_ban_terms",
                    "message": "未从设定中解析到禁止项，已使用默认后期词表抽检",
                }
            )
            bans = list(_DEFAULT_LATE_TERMS)

        # 仅扫第一卷（前期）更有意义：抓提前揭晓
        for num, source, text in texts:
            if num > early_end:
                continue
            for term in bans:
                if term and term in text:
                    hits.append(
                        {
                            "chapter": num,
                            "source": source,
                            "term": term,
                            "severity": "high" if source == "body" else "medium",
                        }
                    )

        # 聚合
        by_term: Dict[str, List[int]] = {}
        for h in hits:
            by_term.setdefault(h["term"], []).append(h["chapter"])
        for term, chs in sorted(by_term.items(), key=lambda x: -len(x[1])):
            uniq = sorted(set(chs))
            alerts.append(
                {
                    "severity": "high" if len(uniq) >= 3 else "medium",
                    "code": "early_forbidden_term",
                    "message": f"前期（≤{early_end}章）出现禁止/后期词「{term}」于第 {', '.join(map(str, uniq[:15]))} 章",
                    "term": term,
                    "chapters": uniq[:40],
                }
            )

        if not hits and texts:
            alerts.append(
                {
                    "severity": "low",
                    "code": "clean",
                    "message": f"前期 {min(early_end, max((n for n,_,_ in texts), default=0))} 章窗口内未命中禁止词（规则数 {len(bans)}）",
                }
            )
        elif not texts:
            alerts.append(
                {
                    "severity": "medium",
                    "code": "no_text",
                    "message": "无可扫描细纲/正文；请先生成内容后再跑铁律",
                }
            )

        alerts.sort(key=lambda a: {"high": 0, "medium": 1, "low": 2}.get(a["severity"], 9))
        high = sum(1 for a in alerts if a["severity"] == "high")
        summary = (
            f"规则 {len(bans)} 条 · 前期窗 1–{early_end} · 命中 {len(hits)} · 预警 {len(alerts)}（高 {high}）"
        )

        report = {
            "plugin": self.meta.id,
            "version": self.meta.version,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "early_end": early_end,
            "ban_terms": bans,
            "hits": hits[:200],
            "alerts": alerts,
            "summary": summary,
            "source_note": "从设定/模板抽取禁止项 + 默认后期词；前期卷窗口扫描",
        }
        out_dir = plugin_report_dir(root, self.meta.id)
        latest = out_dir / "latest.json"
        text = json.dumps(report, ensure_ascii=False, indent=2)
        latest.write_text(text, encoding="utf-8")
        (out_dir / "latest.md").write_text(_to_md(report), encoding="utf-8")

        return PluginRunResult(
            ok=True,
            plugin_id=self.meta.id,
            summary=summary,
            report_path=str(latest.relative_to(root)) if root in latest.parents else str(latest),
            data=report,
            alerts=alerts,
        )


def _to_md(report: Dict[str, Any]) -> str:
    lines = [
        "# 世界观铁律断言",
        "",
        f"- {report.get('summary')}",
        f"- 前期窗口：1–{report.get('early_end')}",
        "",
        "## 规则词",
        "",
        ", ".join(report.get("ban_terms") or []) or "（空）",
        "",
        "## 预警",
        "",
    ]
    for a in report.get("alerts") or []:
        lines.append(f"- **[{a.get('severity')}]** {a.get('message')}")
    lines.append("")
    return "\n".join(lines)
