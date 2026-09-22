# -*- coding: utf-8 -*-
"""
模拟读者：按章阅读正文，提示 AI 味 / 剧情硬伤 / 对话问题，并给建议。
本地启发式全量；可选 LLM 读者人设点评（限章数）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from plugins.base import PluginMeta, PluginRunResult, plugin_report_dir

_DIALOGUE_LINE = re.compile(r'^[「"“].+[」"”]$')
_AI_OPENERS = ("深吸一口气", "夜色如墨", "时光飞逝", "不禁感慨", "与此同时")
_STALL_MARKERS = ("总之", "总而言之", "综上所述", "话说回来", "闲话少说")


class ReaderSimPlugin:
    meta = PluginMeta(
        id="reader_sim",
        name="模拟读者",
        version="0.1.0",
        category="audit",
        description="模拟付费读者读章：AI味/剧情/对话提示与建议",
        premium=False,
        agent_tools=["reader_sim.run"],
    )

    def available(self) -> bool:
        return True

    def run(self, novel_root: Path, **kwargs: Any) -> PluginRunResult:
        root = Path(novel_root)
        start = max(1, int(kwargs.get("start") or 1))
        end = max(start, int(kwargs.get("end") or start + 19))
        use_llm = bool(kwargs.get("use_llm") or kwargs.get("llm"))
        llm_max = max(0, min(int(kwargs.get("llm_max") or 4), 12))
        chapter_only = int(kwargs.get("chapter") or 0)

        bodies = _load_chapters(root, start, end, chapter_only)
        chapters_out: List[Dict[str, Any]] = []
        alerts: List[Dict[str, Any]] = []

        # 先本地全量，再按 AI 分挑章做 LLM
        scored: List[Tuple[int, Dict[str, Any], str]] = []
        for num, title, text in bodies:
            local = _local_reader_pass(num, title, text)
            scored.append((num, local, text))

        llm_budget = llm_max if use_llm else 0
        llm_targets = sorted(
            scored,
            key=lambda x: -int((x[1].get("scores") or {}).get("ai_taste") or 0),
        )[:llm_budget]

        llm_notes: Dict[int, Dict[str, Any]] = {}
        for num, local, text in llm_targets:
            note = _llm_reader_pass(num, local.get("title") or f"第{num}章", text)
            if note:
                llm_notes[num] = note

        for num, local, _text in scored:
            row = dict(local)
            if num in llm_notes:
                row["persona"] = llm_notes[num]
                # 合并建议
                sug = list(row.get("suggestions") or [])
                for s in llm_notes[num].get("suggestions") or []:
                    if s and s not in sug:
                        sug.append(s)
                row["suggestions"] = sug[:8]
                for n in llm_notes[num].get("notes") or []:
                    row.setdefault("issues", []).append(
                        {"kind": "reader", "severity": "medium", "message": str(n)[:200]}
                    )
            chapters_out.append(row)
            for iss in row.get("issues") or []:
                if iss.get("severity") in ("high", "medium"):
                    alerts.append(
                        {
                            "severity": iss.get("severity") or "medium",
                            "code": iss.get("kind") or "reader",
                            "chapter": num,
                            "message": f"第{num}章 · {iss.get('message')}",
                        }
                    )

        chapters_out.sort(key=lambda r: int(r.get("chapter") or 0))
        high = sum(1 for a in alerts if a.get("severity") == "high")
        med = sum(1 for a in alerts if a.get("severity") == "medium")
        need = sum(1 for c in chapters_out if c.get("severity") in ("high", "medium"))
        summary = (
            f"模拟读者 · 扫 {len(chapters_out)} 章 · 需关注 {need} · "
            f"预警高{high}/中{med}"
            + (f" · LLM 点评 {len(llm_notes)} 章" if llm_notes else " · 仅本地")
        )

        report = {
            "plugin": self.meta.id,
            "version": self.meta.version,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "range": {"start": start, "end": end, "chapter": chapter_only or None},
            "use_llm": use_llm,
            "llm_chapters": sorted(llm_notes.keys()),
            "summary": summary,
            "chapters": chapters_out,
            "alerts": alerts[:120],
            "source_note": "本地：反写法词表+对话/节奏启发式；可选 LLM 付费读者人设",
        }
        out_dir = plugin_report_dir(root, self.meta.id)
        latest = out_dir / "latest.json"
        latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "latest.md").write_text(_to_md(report), encoding="utf-8")

        return PluginRunResult(
            ok=True,
            plugin_id=self.meta.id,
            summary=summary,
            report_path=str(latest.relative_to(root)) if root in latest.parents else str(latest),
            data=report,
            alerts=alerts[:80],
        )


def _load_chapters(
    root: Path, start: int, end: int, chapter_only: int
) -> List[Tuple[int, str, str]]:
    from skills.pure.chapter_index import iter_bodies

    out: List[Tuple[int, str, str]] = []
    for num, text, row in iter_bodies(root, min_chars=40, strip_title=False):
        if chapter_only and num != chapter_only:
            continue
        if not chapter_only and (num < start or num > end):
            continue
        title = ""
        if isinstance(row, dict):
            title = str(row.get("title") or "")
        out.append((num, title or f"第{num}章", text or ""))
    out.sort(key=lambda x: x[0])
    return out


def _local_reader_pass(num: int, title: str, text: str) -> Dict[str, Any]:
    from skills.anti_ai.cliche_lexicon import body_tick_cluster, count_cliche_hits
    from skills.pipeline.volume_audit import score_anti_ai

    raw = text or ""
    chars = len(re.sub(r"\s+", "", raw))
    ai = score_anti_ai(raw)
    ai_score = int(ai.get("score") or 0)
    cliches = count_cliche_hits(raw)
    body_n, body_kinds, _ = body_tick_cluster(raw)

    issues: List[Dict[str, Any]] = []
    suggestions: List[str] = []

    # —— AI 味 ——
    if ai_score >= 35:
        issues.append(
            {
                "kind": "ai",
                "severity": "high" if ai_score >= 55 else "medium",
                "message": f"AI 味偏高（本地分 {ai_score}）" + (f"：{', '.join(list(cliches)[:4])}" if cliches else ""),
            }
        )
        suggestions.append("改掉重复生理标签与空转心理词，换成人物特有的小动作。")
    if body_n >= 3 or body_kinds >= 3:
        issues.append(
            {
                "kind": "ai",
                "severity": "high",
                "message": f"身体反应八股扎堆（{body_n}处/{body_kinds}种）",
            }
        )
        suggestions.append("同章身体反应最多偶用一次；紧张用环境/道具折射。")

    # —— 对话 ——
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    dialogue = [ln for ln in lines if _DIALOGUE_LINE.match(ln) or ("「" in ln and "」" in ln)]
    dlg_ratio = (len(dialogue) / max(1, len(lines))) if lines else 0
    # 连续对白无动作
    streak = 0
    max_streak = 0
    for ln in lines:
        if "「" in ln and "」" in ln and len(re.sub(r"\s+", "", ln)) < 80:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    if max_streak >= 6:
        issues.append(
            {
                "kind": "dialogue",
                "severity": "medium",
                "message": f"对白连珠偏长（连续约 {max_streak} 行），读感像剧本对读",
            }
        )
        suggestions.append("对白之间插入半拍微反应（视线/小动作/判断），避免轮流念词。")
    if dlg_ratio > 0.72 and chars > 800:
        issues.append(
            {
                "kind": "dialogue",
                "severity": "low",
                "message": f"对白占比偏高（约 {int(dlg_ratio * 100)}%），场景落地可能不足",
            }
        )

    # —— 剧情/节奏 ——
    for opener in _AI_OPENERS:
        if raw.startswith(opener) or f"\n{opener}" in raw[:200]:
            issues.append(
                {
                    "kind": "plot",
                    "severity": "low",
                    "message": f"开篇套话感：「{opener}…」",
                }
            )
            suggestions.append("开头直接进冲突或未兑现承诺，少用氛围起手。")
            break
    stall_hits = [m for m in _STALL_MARKERS if m in raw]
    if stall_hits:
        issues.append(
            {
                "kind": "plot",
                "severity": "medium",
                "message": f"出现拖戏标记：{', '.join(stall_hits[:3])}",
            }
        )
        suggestions.append("删掉总结腔，改成具体行动推进或信息差兑现。")
    if chars < 600:
        issues.append(
            {
                "kind": "plot",
                "severity": "medium",
                "message": f"本章过短（{chars} 字），付费章容易觉得「水」",
            }
        )
        suggestions.append("补一场有目标的冲突或兑现上章钩子，再收束。")
    elif chars > 9000:
        issues.append(
            {
                "kind": "plot",
                "severity": "low",
                "message": f"本章偏长（{chars} 字），注意中段是否空转",
            }
        )

    # 钩子：末段是否有未完成感
    tail = raw[-400:] if len(raw) > 400 else raw
    hookish = any(x in tail for x in ("？", "!", "！", "忽然", "却", "门外", "消息", "杀机", "不对"))
    if not hookish and chars >= 1200:
        issues.append(
            {
                "kind": "plot",
                "severity": "low",
                "message": "章末钩子偏弱，读者可能没有「下一章」冲动",
            }
        )
        suggestions.append("章末留一个未兑现的问题、威胁或信息差，忌圆满收束。")

    # 综合严重度 / 弃读风险
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    worst = "ok"
    for iss in issues:
        s = str(iss.get("severity") or "low")
        if sev_rank.get(s, 9) < sev_rank.get(worst, 9):
            worst = s
    drop = min(
        100,
        ai_score // 2
        + (25 if any(i.get("severity") == "high" for i in issues) else 0)
        + (12 if any(i.get("kind") == "dialogue" and i.get("severity") != "low" for i in issues) else 0)
        + (10 if any(i.get("kind") == "plot" and i.get("severity") == "medium" for i in issues) else 0),
    )

    scores = {
        "ai_taste": ai_score,
        "drop_risk": drop,
        "dialogue_ratio": round(dlg_ratio, 2),
        "chars": chars,
    }
    return {
        "chapter": num,
        "title": title,
        "severity": worst if issues else "ok",
        "scores": scores,
        "issues": issues,
        "suggestions": suggestions[:6],
        "cliche_hits": cliches,
        "anti_ai_flags": ai.get("flags") or [],
    }


def _llm_reader_pass(num: int, title: str, text: str) -> Optional[Dict[str, Any]]:
    """付费读者人设点评；失败则返回 None。"""
    try:
        from core.key_manager.manager import KeyManager
        from core.llm.gateway import LLMGateway, RuntimeMode
    except Exception:
        return None
    sample = (text or "")[:6000]
    if len(re.sub(r"\s+", "", sample)) < 200:
        return None
    system = (
        "你是网文平台上的挑剔付费读者，不是编辑。读完一章后只挑硬伤："
        "AI味套话、剧情不合理/注水、对话不通顺或像剧本对读。"
        "用 JSON 输出："
        '{"ai_taste":0-10,"plot_ok":0-10,"dialogue_ok":0-10,"drop_risk":0-10,'
        '"notes":["硬伤一句"],"suggestions":["可执行改法一句"]}。'
        "notes/suggestions 各最多 4 条；不要夸、不要复述剧情、不要输出 JSON 以外内容。"
    )
    user = f"第{num}章《{title}》\n\n{sample}"
    try:
        gw = LLMGateway(KeyManager())
        result = gw.chat(
            gw.build_messages(system, user),
            skill="review",
            mode=RuntimeMode.API,
            temperature=0.4,
        )
        raw_text = (getattr(result, "text", None) or getattr(result, "content", None) or "").strip()
        if getattr(result, "error", None) or not raw_text:
            return None
        m = re.search(r"\{[\s\S]*\}", raw_text)
        if not m:
            return None
        data = json.loads(m.group(0))
        if not isinstance(data, dict):
            return None
        return {
            "ai_taste": data.get("ai_taste"),
            "plot_ok": data.get("plot_ok"),
            "dialogue_ok": data.get("dialogue_ok"),
            "drop_risk": data.get("drop_risk"),
            "notes": [str(x)[:200] for x in (data.get("notes") or [])[:4]],
            "suggestions": [str(x)[:200] for x in (data.get("suggestions") or [])[:4]],
        }
    except Exception:
        return None


def _to_md(report: Dict[str, Any]) -> str:
    lines = [
        "# 模拟读者报告",
        "",
        f"- {report.get('summary')}",
        f"- 生成：{report.get('generated_at')}",
        "",
        "## 需关注章节",
        "",
    ]
    for c in report.get("chapters") or []:
        if c.get("severity") not in ("high", "medium"):
            continue
        lines.append(
            f"### 第{c.get('chapter')}章 {c.get('title') or ''} · {c.get('severity')}"
        )
        sc = c.get("scores") or {}
        lines.append(
            f"- AI味 {sc.get('ai_taste')} · 弃读风险 {sc.get('drop_risk')} · 字数 {sc.get('chars')}"
        )
        for iss in c.get("issues") or []:
            lines.append(f"- [{iss.get('kind')}/{iss.get('severity')}] {iss.get('message')}")
        for s in c.get("suggestions") or []:
            lines.append(f"- 建议：{s}")
        persona = c.get("persona") or {}
        for n in persona.get("notes") or []:
            lines.append(f"- 读者：{n}")
        lines.append("")
    if len(lines) <= 8:
        lines.append("（本区间未发现中高风险章）")
        lines.append("")
    return "\n".join(lines)
