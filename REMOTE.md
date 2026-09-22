# Novel-plugin 远程仓布局

| 镜像 | 地址 |
|------|------|
| GitHub | https://github.com/jiukemi/Novel-plugin |
| Gitee | https://gitee.com/webhwh/novel-plugin |

## 仓根（不是 App 运行时 plugins/）

```
catalog.json          # App hosts.catalog_url 指向这里
plugins/<id>/         # 插件源码
packages/*.zip        # 代码包（download_urls 直链，保证未发 Release 也能下）
PROTOCOL.md
```

App 内 `plugins/` 含 registry/store 等运行时，**不要**整树推到本仓。

## Releases（可选，大权重）

```
tag: assets-v1
  laya-ml-weights.safetensors
```
