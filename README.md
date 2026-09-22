# Novel-plugin

九易 AI 小说引擎 · 插件广场仓库（GitHub / Gitee 双端同步）

| | |
|--|--|
| GitHub | https://github.com/jiukemi/Novel-plugin |
| Gitee | https://gitee.com/webhwh/novel-plugin |

## 布局

```
catalog.json          # 广场目录（App 拉取）
plugins/<id>/         # 插件源码
packages/*.zip        # 可安装代码包（小文件；权重另走 Release/CDN）
PROTOCOL.md
```

安装：App → 插件广场 → 安装。runtime（如 Laya）需再下权重。
