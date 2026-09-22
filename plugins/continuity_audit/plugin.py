# -*- coding: utf-8 -*-
"""
跨层连贯巡检：结构 / 大纲 / 细纲 / 正文覆盖对账（本地启发式，不调 LLM）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from plugins.base import PluginMeta, PluginRunResult, plugin_report_dir


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _body_done_numbers(novel_root: Path) -> Set[int]:
    from skills.pure.chapter_index import done_numbers

    return done_numbers(novel_root, min_chars=200)


class ContinuityAuditPlugin:
    meta = PluginMeta(
        id="continuity_audit",
        name="跨层连贯巡检",
        version="0.1.0",
        category="continuity",
        description="结构↔大纲↔细纲↔正文覆盖对账，抓缺卷、空细纲、锚点覆盖缺口",
        premium=False,
        agent_tools=["continuity_audit.run"],
    )

    def available(self) -> bool:
        return True

    def run(self, novel_root: Path, **kwargs: Any) -> PluginRunResult:
        root = Path(novel_root)
        structure = _load_json(root / "structure" / "structure.json")
        outline = _load_json(root / "outlines" / "outline.json")
        detail = _load_json(root / "details" / "detail.json")
        project = _load_json(root / "project.json")

        alerts: List[Dict[str, Any]] = []
        layers: Dict[str, Any] = {}

        # —— 结构卷 ——
        struct_vols = structure.get("volumes") or []
        if not struct_vols and (project.get("volumes") or []):
            struct_vols = project.get("volumes") or []
        layers["structure_volumes"] = len(struct_vols)

        # —— 大纲卷 ——
        outline_vols = outline.get("volumes") or []
        layers["outline_volumes"] = len(outline_vols)
        outline_filled = sum(1 for v in outline_vols if (v.get("outline") or "").strip())
        layers["outline_filled"] = outline_filled
        if outline_vols and outline_filled == 0:
            alerts.append(
                {
                    "severity": "high",
                    "code": "outline_all_empty",
                    "message": "已有分卷但大纲全文为空",
                }
            )
        for v in outline_vols:
            name = v.get("name") or v.get("id") or "未命名卷"
            if not (v.get("outline") or "").strip():
                alerts.append(
                    {
                        "severity": "medium",
                        "code": "outline_volume_empty",
                        "message": f"大纲空卷：「{name}」",
                        "volume": name,
                    }
                )

        # 结构卷 vs 大纲卷数量
        if struct_vols and outline_vols and abs(len(struct_vols) - len(outline_vols)) >= 1:
            alerts.append(
                {
                    "severity": "medium",
                    "code": "volume_count_mismatch",
                    "message": f"结构卷数 {len(struct_vols)} 与大纲卷数 {len(outline_vols)} 不一致",
                }
            )

        # —— 细纲 ——
        dchs = detail.get("chapters") or []
        detail_done = [
            c
            for c in dchs
            if (c.get("content") or "").strip()
            or (isinstance(c.get("fields"), dict) and c.get("fields"))
        ]
        layers["detail_slots"] = len(dchs)
        layers["detail_filled"] = len(detail_done)

        # 大纲覆盖章范围 vs 细纲填充
        covered_ranges: List[Tuple[int, int, str]] = []
        for v in outline_vols:
            start = int(v.get("start") or 0)
            end = int(v.get("end") or 0)
            name = str(v.get("name") or "")
            if start > 0 and end >= start:
                covered_ranges.append((start, end, name))
                has_outline = bool((v.get("outline") or "").strip())
                filled_in_vol = [
                    c
                    for c in detail_done
                    if start <= int(c.get("number") or 0) <= end
                ]
                if has_outline and not filled_in_vol:
                    alerts.append(
                        {
                            "severity": "high",
                            "code": "detail_gap_after_outline",
                            "message": f"「{name}」已有大纲，但细纲 第{start}–{end}章 全空",
                            "volume": name,
                            "start": start,
                            "end": end,
                        }
                    )
                elif has_outline:
                    ratio = len(filled_in_vol) / max(1, end - start + 1)
                    if ratio < 0.2 and (end - start + 1) >= 10:
                        alerts.append(
                            {
                                "severity": "medium",
                                "code": "detail_sparse",
                                "message": f"「{name}」细纲覆盖偏低（{len(filled_in_vol)}/{end - start + 1} 章）",
                                "volume": name,
                            }
                        )

        # —— 正文 ——
        body_done = _body_done_numbers(root)
        layers["body_filled"] = len(body_done)
        # 有正文但无细纲
        detail_nums = {int(c.get("number") or 0) for c in detail_done}
        body_without_detail = sorted(n for n in body_done if n not in detail_nums)[:20]
        if body_without_detail:
            alerts.append(
                {
                    "severity": "medium",
                    "code": "body_without_detail",
                    "message": f"有正文却无细纲的章（示例）：{', '.join(map(str, body_without_detail[:12]))}",
                    "chapters": body_without_detail,
                }
            )

        # 细纲残留章名但无内容（上次删除残留）
        orphan_titles = []
        for c in dchs:
            n = int(c.get("number") or 0)
            default = f"第{n}章"
            title = (c.get("title") or "").strip()
            has_body = bool((c.get("content") or "").strip() or c.get("fields"))
            if not has_body and title and title != default:
                orphan_titles.append(n)
        if orphan_titles:
            alerts.append(
                {
                    "severity": "low",
                    "code": "orphan_detail_titles",
                    "message": f"{len(orphan_titles)} 章仅残留自定义章名、无细纲内容",
                    "chapters": orphan_titles[:30],
                }
            )

        # 故事梗概
        if not (outline.get("story_summary") or "").strip():
            alerts.append(
                {
                    "severity": "low",
                    "code": "missing_story_summary",
                    "message": "故事梗概为空，跨卷连贯缺少总锚点",
                }
            )

        alerts.sort(key=lambda a: {"high": 0, "medium": 1, "low": 2}.get(a["severity"], 9))
        high = sum(1 for a in alerts if a["severity"] == "high")
        summary = (
            f"结构{layers['structure_volumes']}卷 · 大纲{layers['outline_filled']}/{layers['outline_volumes']} · "
            f"细纲{layers['detail_filled']}/{layers['detail_slots']} · 正文{layers['body_filled']}章 · "
            f"预警 {len(alerts)}（高 {high}）"
        )

        report = {
            "plugin": self.meta.id,
            "version": self.meta.version,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "layers": layers,
            "alerts": alerts,
            "summary": summary,
            "source_note": "本地跨层覆盖对账；不做语义剧情推理",
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
    lines = ["# 跨层连贯巡检", "", f"- {report.get('summary')}", "", "## 层级", ""]
    for k, v in (report.get("layers") or {}).items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## 预警", ""]
    alerts = report.get("alerts") or []
    if not alerts:
        lines.append("无预警。")
    else:
        for a in alerts:
            lines.append(f"- **[{a.get('severity')}]** {a.get('message')}")
    lines.append("")
    return "\n".join(lines)
