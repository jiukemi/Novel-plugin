# -*- coding: utf-8 -*-
"""
本地决策 · Laya（runtime）

- 插件代码：按需从广场安装到 data/plugins/installed/decision_laya
- 权重：单独下载到 data/plugins/assets/decision_laya/（不塞进 zip）
- 能力：choice / score / bool → 概率；供落实检查、提案分流等调用

当前推理后端：权重就绪后走本地编码器接口占位；未就绪时返回明确错误（不回退瞎猜）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.paths import DATA_DIR
from plugins.base import PluginMeta, PluginRunResult, plugin_report_dir

ASSET_DIR = DATA_DIR / "plugins" / "assets" / "decision_laya"
WEIGHTS_NAME = "laya-ml-weights.safetensors"


class DecisionLayaPlugin:
    meta = PluginMeta(
        id="decision_laya",
        name="本地决策 · Laya",
        version="0.1.0",
        category="infra",
        description="开源决策层（choice / score / bool），代码与权重拆分",
        premium=False,
        agent_tools=["decision_laya.decide"],
        hidden=False,
    )

    def available(self) -> bool:
        return True

    def assets_ready(self) -> bool:
        return (ASSET_DIR / WEIGHTS_NAME).is_file() and (ASSET_DIR / WEIGHTS_NAME).stat().st_size > 1024

    def assets_status(self) -> Dict[str, Any]:
        path = ASSET_DIR / WEIGHTS_NAME
        return {
            "ready": self.assets_ready(),
            "dir": str(ASSET_DIR),
            "weights": WEIGHTS_NAME,
            "path": str(path) if path.exists() else None,
            "size_bytes": path.stat().st_size if path.is_file() else 0,
        }

    def decide(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        payload:
          type: choice | score | bool
          context: str
          options: list[str]  (choice)
          scale: [lo, hi]     (score)
          statement: str      (bool)
        """
        payload = payload or {}
        if not self.assets_ready():
            return {
                "ok": False,
                "error": "weights_missing",
                "message": "决策权重未下载。请在插件广场安装本插件后，点击「下载权重」。",
                "assets": self.assets_status(),
            }
        dtype = str(payload.get("type") or "choice").lower()
        context = str(payload.get("context") or "").strip()
        # 权重已就绪：预留本地推理入口（当前返回占位分布，待接 Laya/Transformers）
        if dtype == "choice":
            options = [str(x) for x in (payload.get("options") or []) if str(x).strip()]
            if not options:
                return {"ok": False, "error": "bad_request", "message": "choice 需要 options"}
            if len(options) > 20:
                return {
                    "ok": False,
                    "error": "too_many_options",
                    "message": "Laya 建议选项 ≤20；请先做粗分层级。",
                }
            n = len(options)
            # 均匀先验占位：接入真实模型后替换为 logits→softmax
            probs = {o: round(1.0 / n, 6) for o in options}
            best = options[0]
            return {
                "ok": True,
                "type": "choice",
                "best": best,
                "probs": probs,
                "confidence": probs[best],
                "backend": "laya_stub",
                "context_chars": len(context),
                "note": "权重已就绪；推理后端待接真实 Laya 编码器。",
            }
        if dtype == "score":
            scale = payload.get("scale") or [0, 5]
            lo, hi = float(scale[0]), float(scale[1])
            mid = (lo + hi) / 2.0
            return {
                "ok": True,
                "type": "score",
                "score": mid,
                "scale": [lo, hi],
                "confidence": 0.5,
                "backend": "laya_stub",
                "note": "权重已就绪；推理后端待接真实 Laya 编码器。",
            }
        if dtype in ("bool", "noul", "boolean"):
            return {
                "ok": True,
                "type": "bool",
                "prob_true": 0.5,
                "confidence": 0.5,
                "backend": "laya_stub",
                "statement": str(payload.get("statement") or context),
                "note": "权重已就绪；推理后端待接真实 Laya 编码器。",
            }
        return {"ok": False, "error": "bad_type", "message": f"未知 type: {dtype}"}

    def run(self, novel_root: Path, **kwargs: Any) -> PluginRunResult:
        """自检：资产状态 + 可选试决策。"""
        out_dir = plugin_report_dir(novel_root, "decision_laya")
        assets = self.assets_status()
        trial = None
        if kwargs.get("trial"):
            trial = self.decide(
                {
                    "type": "choice",
                    "context": str(kwargs.get("context") or "落实检查：本章节拍是否写全"),
                    "options": kwargs.get("options")
                    or ["expand", "accept", "regen"],
                }
            )
        data = {
            "assets": assets,
            "trial": trial,
            "protocol": "plaza.v1",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        report = out_dir / "latest.json"
        report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        md = out_dir / "latest.md"
        lines = [
            "# 本地决策 · Laya",
            "",
            f"- 权重就绪：{'是' if assets['ready'] else '否'}",
            f"- 资产目录：`{assets['dir']}`",
            "",
        ]
        if not assets["ready"]:
            lines.append("请在插件广场点击「下载权重」后再用于分流。")
        elif trial:
            lines.append(f"试决策：`{json.dumps(trial, ensure_ascii=False)}`")
        md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok = True
        summary = (
            "Laya 权重已就绪，可做本地决策"
            if assets["ready"]
            else "插件代码已装，权重未下载（广场内点「下载权重」）"
        )
        return PluginRunResult(
            ok=ok,
            plugin_id="decision_laya",
            summary=summary,
            report_path=str(report),
            data=data,
            alerts=[]
            if assets["ready"]
            else [
                {
                    "severity": "info",
                    "code": "weights_missing",
                    "message": "决策权重未下载",
                }
            ],
        )
