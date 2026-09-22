# 九易插件协议（Plugin Plaza v1）

未安装 / 已禁用时 App **静默回退老流程**，不阻断写章。

## 包形态

| 目录 | 用途 |
|------|------|
| `plugins/<id>/` | 随 App 发布的内置扫描插件（可选安装到 `data/plugins/installed`） |
| `plugins/plaza/<id>/` | **广场源码包**：默认不加载，用户按需安装 |
| `plugins/market/` | 本地目录 `catalog.json` + `hosts.json` + 可选 `packages/*.zip` |
| `data/plugins/installed/<id>/` | 用户已安装的插件代码 |
| `data/plugins/assets/<id>/` | 大资源（权重等），与代码分离 |

## plugin.json

```json
{
  "id": "decision_laya",
  "name": "本地决策 · Laya",
  "version": "0.1.0",
  "kind": "runtime",
  "category": "infra",
  "description": "…",
  "premium": false,
  "entry": "plugin:DecisionLayaPlugin",
  "agent_tools": [],
  "hidden": false,
  "builtin": false,
  "plaza": true,
  "assets": [
    {
      "id": "weights_zh",
      "name": "Laya 多语权重",
      "filename": "laya-ml-weights.safetensors",
      "size_hint_mb": 900,
      "required": true,
      "sha256": "",
      "urls": {
        "gitee": "https://gitee.com/jiuyi-ai/jy-novel-plugins/releases/download/assets-v1/laya-ml-weights.safetensors",
        "github": "https://github.com/jiuyi-ai/jy-novel-plugins/releases/download/assets-v1/laya-ml-weights.safetensors"
      }
    }
  ]
}
```

### kind

| kind | 含义 |
|------|------|
| `scan` | 本地规则巡检 → 报告 / 提案（默认） |
| `agent` | 依赖 Code Agent / 外挂大脑 |
| `runtime` | 本地运行时（决策引擎等）；**代码与大资源拆分下载** |

### category

`cast` | `continuity` | `ledger` | `router` | `audit` | `infra`

## 镜像托管

`plugins/market/hosts.json`（已绑定）：

| 镜像 | 仓 |
|------|-----|
| GitHub | https://github.com/jiukemi/Novel-plugin |
| Gitee | https://gitee.com/webhwh/novel-plugin |
| CDN | 可选；付费包 / 大权重 |

- `prefer`: `gitee` | `github` | `cdn` | `auto`（国内优先 Gitee）
- 每个镜像提供 `catalog_url` 与 `release_base`
- 包级可用 `download_urls.{gitee,github,cdn}` 写完整 zip 地址（付费包推荐）
- 安装顺序：prefer 序 → 本地 `market/packages` → `plaza/` 源码目录

GitHub Releases 与 Gitee Releases **同 tag、同 asset 名**同步；`catalog.json` 两边 raw 同步。

## 运行时约定

- `available()`：代码可加载且已启用
- `assets_ready()`（可选）：必要权重已落盘
- `run(novel_root, **params)`：巡检类
- `decide(payload)`（runtime）：`{type: choice|score|bool, …}` → 概率分布；无权重时返回明确错误，不假装推理

## 安全

- zip 路径禁止 `..` / 绝对路径
- 插件代码包默认上限 16MB；大资源走 `assets` 流式落盘
- 仅允许 https 下载；校验可选 sha256
